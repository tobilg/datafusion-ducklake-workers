use crate::api::ApiError;
use base64::Engine;
use datafusion::arrow::{
    array::*,
    datatypes::{DataType, TimeUnit},
    record_batch::RecordBatch,
};
use std::io::Write;

struct Capped {
    bytes: Vec<u8>,
    cap: usize,
}
impl Write for Capped {
    fn write(&mut self, b: &[u8]) -> std::io::Result<usize> {
        if b.len() > self.cap.saturating_sub(self.bytes.len()) {
            return Err(std::io::Error::other("result budget"));
        }
        self.bytes.extend_from_slice(b);
        Ok(b.len())
    }
    fn flush(&mut self) -> std::io::Result<()> {
        Ok(())
    }
}
fn json(w: &mut Capped, value: impl serde::Serialize) -> Result<(), ApiError> {
    serde_json::to_writer(w, &value).map_err(|_| ApiError::resource())
}
fn supported(t: &DataType) -> bool {
    use DataType::*;
    matches!(
        t,
        Null | Boolean
            | Int8
            | Int16
            | Int32
            | Int64
            | UInt8
            | UInt16
            | UInt32
            | UInt64
            | Float32
            | Float64
            | Decimal128(_, _)
            | Decimal256(_, _)
            | Utf8
            | LargeUtf8
            | Utf8View
            | Binary
            | LargeBinary
            | BinaryView
            | FixedSizeBinary(_)
            | Date32
            | Date64
            | Timestamp(_, _)
            | Time32(TimeUnit::Second | TimeUnit::Millisecond)
            | Time64(TimeUnit::Microsecond | TimeUnit::Nanosecond)
            | Duration(_)
    )
}
fn cell(w: &mut Capped, a: &dyn Array, i: usize) -> Result<(), ApiError> {
    if a.is_null(i) {
        return json(w, Option::<bool>::None);
    }
    macro_rules! val {
        ($t:ty) => {
            a.as_any()
                .downcast_ref::<$t>()
                .ok_or_else(ApiError::resource)?
                .value(i)
        };
    }
    macro_rules! num {
        ($t:ty) => {
            json(w, val!($t))
        };
    }
    macro_rules! string_num {
        ($t:ty) => {
            json(w, val!($t).to_string())
        };
    }
    use DataType::*;
    match a.data_type() {
        Null => json(w, Option::<bool>::None),
        Boolean => num!(BooleanArray),
        Int8 => num!(Int8Array),
        Int16 => num!(Int16Array),
        Int32 => num!(Int32Array),
        Int64 => string_num!(Int64Array),
        UInt8 => num!(UInt8Array),
        UInt16 => num!(UInt16Array),
        UInt32 => num!(UInt32Array),
        UInt64 => string_num!(UInt64Array),
        Float32 => {
            let v = val!(Float32Array);
            if !v.is_finite() {
                return Err(ApiError::unsupported());
            }
            json(w, v)
        }
        Float64 => {
            let v = val!(Float64Array);
            if !v.is_finite() {
                return Err(ApiError::unsupported());
            }
            json(w, v)
        }
        Utf8 => json(w, val!(StringArray)),
        LargeUtf8 => json(w, val!(LargeStringArray)),
        Utf8View => json(w, val!(StringViewArray)),
        Binary | LargeBinary | BinaryView | FixedSizeBinary(_) => {
            let b = match a.data_type() {
                Binary => val!(BinaryArray),
                LargeBinary => val!(LargeBinaryArray),
                BinaryView => val!(BinaryViewArray),
                _ => val!(FixedSizeBinaryArray),
            };
            if b.len().saturating_add(2) / 3 * 4 + 2 > w.cap.saturating_sub(w.bytes.len()) {
                return Err(ApiError::resource());
            }
            json(w, base64::engine::general_purpose::STANDARD.encode(b))
        }
        // Arrow's scalar decimal display retains the declared scale, without f64 conversion.
        Decimal128(_, _) | Decimal256(_, _) => json(
            w,
            datafusion::arrow::util::display::array_value_to_string(a, i)
                .map_err(|_| ApiError::unsupported())?,
        ),
        Date32 => string_num!(Date32Array),
        Date64 => string_num!(Date64Array),
        Time32(TimeUnit::Second) => string_num!(Time32SecondArray),
        Time32(TimeUnit::Millisecond) => string_num!(Time32MillisecondArray),
        Time64(TimeUnit::Microsecond) => string_num!(Time64MicrosecondArray),
        Time64(TimeUnit::Nanosecond) => string_num!(Time64NanosecondArray),
        Timestamp(TimeUnit::Second, _) => string_num!(TimestampSecondArray),
        Timestamp(TimeUnit::Millisecond, _) => string_num!(TimestampMillisecondArray),
        Timestamp(TimeUnit::Microsecond, _) => string_num!(TimestampMicrosecondArray),
        Timestamp(TimeUnit::Nanosecond, _) => string_num!(TimestampNanosecondArray),
        Duration(TimeUnit::Second) => string_num!(DurationSecondArray),
        Duration(TimeUnit::Millisecond) => string_num!(DurationMillisecondArray),
        Duration(TimeUnit::Microsecond) => string_num!(DurationMicrosecondArray),
        Duration(TimeUnit::Nanosecond) => string_num!(DurationNanosecondArray),
        _ => Err(ApiError::unsupported()),
    }
}

pub struct Output {
    buffer: Capped,
    row_cap: usize,
    max_rows: usize,
    pub rows: usize,
    pub truncated: bool,
}
impl Output {
    pub fn new(
        id: &str,
        schema: &datafusion::arrow::datatypes::Schema,
        max_rows: usize,
        max_bytes: usize,
    ) -> Result<Self, ApiError> {
        if schema.fields().len() > 256 || schema.fields().iter().any(|f| !supported(f.data_type()))
        {
            return Err(ApiError::unsupported());
        }
        let mut buffer = Capped {
            bytes: Vec::new(),
            cap: max_bytes.saturating_sub(128),
        };
        buffer
            .write_all(b"{\"request_id\":")
            .map_err(|_| ApiError::resource())?;
        json(&mut buffer, id)?;
        buffer
            .write_all(b",\"columns\":[")
            .map_err(|_| ApiError::resource())?;
        for (i, f) in schema.fields().iter().enumerate() {
            if i > 0 {
                buffer.write_all(b",").map_err(|_| ApiError::resource())?;
            }
            json(
                &mut buffer,
                serde_json::json!({"name":f.name(),"type":format!("{:?}",f.data_type())}),
            )?;
        }
        buffer
            .write_all(b"],\"rows\":[")
            .map_err(|_| ApiError::resource())?;
        let row_cap = buffer.cap.saturating_sub(buffer.bytes.len());
        Ok(Self {
            buffer,
            row_cap,
            max_rows,
            rows: 0,
            truncated: false,
        })
    }
    /// False means stop and drop the upstream stream immediately.
    pub fn row(&mut self, batch: &RecordBatch, i: usize) -> Result<bool, ApiError> {
        if self.rows == self.max_rows {
            self.truncated = true;
            return Ok(false);
        }
        let mut row = Capped {
            bytes: Vec::new(),
            cap: self.row_cap,
        };
        row.write_all(b"[").map_err(|_| ApiError::resource())?;
        for (n, array) in batch.columns().iter().enumerate() {
            if n > 0 {
                row.write_all(b",").map_err(|_| ApiError::resource())?;
            }
            cell(&mut row, array.as_ref(), i)?;
        }
        row.write_all(b"]").map_err(|_| ApiError::resource())?;
        if row.bytes.len() + usize::from(self.rows > 0)
            > self.buffer.cap.saturating_sub(self.buffer.bytes.len())
        {
            self.truncated = true;
            return Ok(false);
        }
        if self.rows > 0 {
            self.buffer
                .write_all(b",")
                .map_err(|_| ApiError::resource())?;
        }
        self.buffer
            .write_all(&row.bytes)
            .map_err(|_| ApiError::resource())?;
        self.rows += 1;
        Ok(true)
    }
    pub fn finish(mut self, elapsed_ms: u128) -> Result<Vec<u8>, ApiError> {
        self.buffer.cap += 128;
        write!(
            &mut self.buffer,
            "],\"row_count\":{},\"truncated\":{},\"elapsed_ms\":{}}}",
            self.rows, self.truncated, elapsed_ms
        )
        .map_err(|_| ApiError::resource())?;
        Ok(self.buffer.bytes)
    }
}
