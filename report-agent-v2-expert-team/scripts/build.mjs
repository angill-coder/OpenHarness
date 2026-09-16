import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';

const python = [process.env.PYTHON, 'python3', 'python'].filter(Boolean).find(command =>
  spawnSync(command, ['-c', 'import sys; sys.exit(0 if sys.version_info >= (3,9) else 1)'],
    {timeout:10000}).status === 0);
if(!python)throw new Error('Building requires Python 3.9+; set PYTHON to its executable.');
const result = spawnSync(python, [fileURLToPath(new URL('./build.py', import.meta.url))],
  {stdio:'inherit'});
if(result.error)throw result.error;
process.exit(result.status ?? 1);
