# chpbench

Benchmarking utilities and scripts for configurable-http-proxy.

This consists of two pieces, a worker and a runner.

## Worker

worker.py is a simple tornado server that handles HTTP and websocket requests.
On startup, it adds itself to the proxy via the proxy's REST API.

For HTTP requests, url parameters can govern behavior:

- `size` sets the size (in bytes) of the body of the reply (default: 0).
- `delay` adds an artificial delay (in seconds) before sending the reply (default: 0)

Any URL ending with websocket messages are parsed as JSON and echoed back without modification.
If the message contains a `delay` field, an artificial delay will be added before replying to the message.


## Runner

runner.py contains some utilities and a script for performing a test. It:

1. starts a proxy
2. starts `w` workers behind the proxy and adds them to the proxy
3. runs a test, making requests to random endpoints

The test is run twice, once with raw URLs bypassing the proxy, and again via the proxy,
so the proxy's contribution can be measured.

The last step should probably be run with something like apache-bench instead of our own runner.

## TODO

- Run on Docker Swarm / kubernetes for real-world multi-node testing.
- Websocket tests
