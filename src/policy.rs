//! Both the parsed statement and every resolved plan (including subqueries) are checked.
use crate::api::ApiError;
use crate::config::QueryMode;
use datafusion::{
    common::tree_node::TreeNodeRecursion, datasource::source_as_provider, logical_expr::LogicalPlan,
};
#[cfg(feature = "ducklake")]
use datafusion_ducklake_provider::DuckLakeTable;
#[cfg(feature = "ducklake")]
use ducklake_storage::quack::QuackCatalog;
use sqlparser::{
    ast::{
        BinaryOperator, Expr, ObjectName, Query, Select, Statement, TableFactor, Visit, Visitor,
    },
    dialect::GenericDialect,
    parser::Parser,
};
use std::ops::ControlFlow;
use std::sync::Arc;

pub fn function_allowed(name: &str) -> bool {
    matches!(
        name,
        "count"
            | "sum"
            | "avg"
            | "min"
            | "max"
            | "abs"
            | "round"
            | "ceil"
            | "floor"
            | "coalesce"
            | "nullif"
            | "lower"
            | "upper"
            | "length"
            | "char_length"
            | "octet_length"
            | "substr"
            | "substring"
            | "trim"
            | "ltrim"
            | "rtrim"
            | "starts_with"
            | "ends_with"
            | "date_trunc"
            | "date_part"
            | "now"
            | "current_date"
            | "current_time"
            | "current_timestamp"
            | "row_number"
            | "rank"
            | "dense_rank"
            | "lag"
            | "lead"
            | "first_value"
            | "last_value"
    )
}
pub fn parse(sql: &str, mode: QueryMode) -> Result<Vec<String>, ApiError> {
    let statements = Parser::new(&GenericDialect)
        .with_recursion_limit(64)
        .try_with_sql(sql)
        .and_then(|mut p| p.parse_statements())
        .map_err(|_| ApiError::input("INVALID_SQL", "SQL could not be parsed"))?;
    if statements.len() != 1 {
        return Err(ApiError::forbidden());
    }
    let mut visitor = ReadOnly {
        nodes: 0,
        query_depth: 0,
        expression_depth: 0,
        mode,
        sources: Vec::new(),
    };
    match statements[0].visit(&mut visitor) {
        ControlFlow::Continue(()) => Ok(visitor.sources),
        ControlFlow::Break(e) => Err(e),
    }
}
struct ReadOnly {
    nodes: usize,
    query_depth: usize,
    expression_depth: usize,
    mode: QueryMode,
    sources: Vec<String>,
}
impl ReadOnly {
    fn tick(&mut self) -> ControlFlow<ApiError> {
        self.nodes += 1;
        if self.nodes > 2048 {
            ControlFlow::Break(ApiError::resource())
        } else {
            ControlFlow::Continue(())
        }
    }
}
impl Visitor for ReadOnly {
    type Break = ApiError;
    fn pre_visit_statement(&mut self, s: &Statement) -> ControlFlow<ApiError> {
        if !matches!(s, Statement::Query(_)) {
            return ControlFlow::Break(ApiError::forbidden());
        }
        self.tick()
    }
    fn pre_visit_query(&mut self, q: &Query) -> ControlFlow<ApiError> {
        // Planner recursion has larger frames than the SQL parser. Bound it
        // before planning: a 16-level derived query trapped the 256 KiB stack.
        self.query_depth += 1;
        if self.query_depth > 8 {
            return ControlFlow::Break(ApiError::resource());
        }
        if !q.locks.is_empty()
            || q.for_clause.is_some()
            || q.settings.is_some()
            || q.format_clause.is_some()
            || !q.pipe_operators.is_empty()
            || q.with.as_ref().is_some_and(|w| w.recursive)
        {
            return ControlFlow::Break(ApiError::forbidden());
        }
        self.tick()
    }
    fn post_visit_query(&mut self, _: &Query) -> ControlFlow<ApiError> {
        self.query_depth -= 1;
        ControlFlow::Continue(())
    }
    fn pre_visit_select(&mut self, s: &Select) -> ControlFlow<ApiError> {
        if s.into.is_some() || !s.lateral_views.is_empty() || !s.connect_by.is_empty() {
            return ControlFlow::Break(ApiError::forbidden());
        }
        self.tick()
    }
    fn pre_visit_relation(&mut self, n: &ObjectName) -> ControlFlow<ApiError> {
        let parts: Option<Vec<_>> = n.0.iter().map(|p| p.as_ident()).collect();
        let Some(parts) = parts else {
            return ControlFlow::Break(ApiError::forbidden());
        };
        if self.mode == QueryMode::Files && parts.len() == 1 && parts[0].value.contains("://") {
            let value = &parts[0].value;
            if parts[0].quote_style.is_none()
                || !matches!(
                    value.split("://").next(),
                    Some("s3" | "r2" | "https" | "http")
                )
            {
                return ControlFlow::Break(ApiError::forbidden());
            }
            if !self.sources.contains(value) {
                self.sources.push(value.clone());
            }
            if self.sources.len() > 16 {
                return ControlFlow::Break(ApiError::resource());
            }
            return self.tick();
        }
        if parts.len() > 3
            || (parts.len() == 3 && parts[0].value != "lake")
            || parts.iter().any(|p| {
                p.value.is_empty()
                    || p.value
                        .chars()
                        .any(|c| c.is_control() || "/\\:%".contains(c))
            })
        {
            return ControlFlow::Break(ApiError::forbidden());
        }
        self.tick()
    }
    fn pre_visit_table_factor(&mut self, t: &TableFactor) -> ControlFlow<ApiError> {
        match t {
            TableFactor::Table {
                args: None,
                version: None,
                json_path: None,
                sample: None,
                with_hints,
                ..
            } if with_hints.is_empty() => {}
            TableFactor::Derived { sample: None, .. } | TableFactor::NestedJoin { .. } => {}
            _ => return ControlFlow::Break(ApiError::forbidden()),
        }
        self.tick()
    }
    fn pre_visit_expr(&mut self, e: &Expr) -> ControlFlow<ApiError> {
        self.expression_depth += 1;
        if self.expression_depth > 32 {
            return ControlFlow::Break(ApiError::resource());
        }
        // CTEs can repeatedly double strings before the output cap or pool sees them.
        // Keep concatenation out of this pilot's expression subset.
        if matches!(
            e,
            Expr::BinaryOp {
                op: BinaryOperator::StringConcat,
                ..
            }
        ) {
            return ControlFlow::Break(ApiError::forbidden());
        }
        if let Expr::Function(f) = e {
            if !function_allowed(&f.name.to_string().to_ascii_lowercase()) {
                return ControlFlow::Break(ApiError::forbidden());
            }
        }
        self.tick()
    }
    fn post_visit_expr(&mut self, _: &Expr) -> ControlFlow<ApiError> {
        self.expression_depth -= 1;
        ControlFlow::Continue(())
    }
}

pub fn plan(
    plan: &LogicalPlan,
    mode: QueryMode,
    providers: &[Arc<dyn datafusion::catalog::TableProvider>],
) -> Result<(), ApiError> {
    use LogicalPlan::*;
    let mut count = 0usize;
    plan.apply_with_subqueries(|p| {
        count += 1;
        let allowed = count <= 2048
            && match p {
                Projection(_) | Filter(_) | Window(_) | Aggregate(_) | Sort(_) | Join(_)
                | Union(_) | EmptyRelation(_) | Subquery(_) | SubqueryAlias(_) | Limit(_)
                | Values(_) | Distinct(_) => true,
                TableScan(scan) => source_as_provider(&scan.source).is_ok_and(|provider| {
                    if mode == QueryMode::Files {
                        providers
                            .iter()
                            .any(|approved| Arc::ptr_eq(approved, &provider))
                    } else {
                        #[cfg(feature = "ducklake")]
                        {
                            scan.table_name.catalog().is_none_or(|c| c == "lake")
                                && provider.is::<DuckLakeTable<QuackCatalog>>()
                        }
                        #[cfg(not(feature = "ducklake"))]
                        {
                            false
                        }
                    }
                }),
                _ => false,
            };
        if allowed {
            Ok(TreeNodeRecursion::Continue)
        } else {
            Err(datafusion::error::DataFusionError::Plan(
                "Query policy".into(),
            ))
        }
    })
    .map_err(|_| ApiError::forbidden())?;
    Ok(())
}
