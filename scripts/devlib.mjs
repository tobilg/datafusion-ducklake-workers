// Local outputs are disposable and never inputs to a release claim.
import {mkdirSync} from 'node:fs';
mkdirSync(new URL('../.cache/reports/', import.meta.url), {recursive: true});
