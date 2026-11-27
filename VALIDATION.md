# GateStream v2.0.34 validation

| Check | Result for GitHub |
| --- | --- |
| Python 3.11.13 on Linux | 144 passed |
| Python 3.12.14 on Linux | 144 passed |
| Cloudflare Worker suite on Node 24 | 8 passed |

Run the Python suite with `python -m unittest discover -s tests -v`. Run `node --test cloudflare/worker.test.mjs` from the GitHub package for the Worker suite.

A local Python 3.11.13 comparison used 300 cached HTTP range responses with mocked
file metadata and cache reads. Both versions returned the same requested bytes
and HTTP 206 status. The old build ran 300 full garbage collections and
v2.0.34 ran zero during the comparison. A separate simulated 4,096-chunk Telegram
read eliminated the 4,097 helper tasks created for reads and EOF in the old
build. These checks isolate Python overhead. Actual throughput, CPU and RAM on
Serv00 depend on Telegram, the network and the number of active connections.
The packages were tested on Linux.
