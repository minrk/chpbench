#!/usr/bin/env python3

import asyncio
import atexit
from binascii import hexlify
from concurrent.futures import ThreadPoolExecutor
import inspect
import json
import os
import socket
from subprocess import Popen
import sys
import time
from urllib.request import urlopen


Executor = ThreadPoolExecutor
# Executor = ProcessPoolExecutor  # uncomment to run in processes instead of threads

from jupyterhub.proxy import ConfigurableHTTPProxy
import numpy as np
import requests

from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect
from traitlets.config import Config

here = os.path.dirname(__file__)
worker_py = os.path.join(here, 'worker.py')

try:
    TimeoutError
except NameError:
    # py2-compat
    class TimeoutError(Exception):
        pass


def random_ports(n):
    """Return n random ports that are available."""
    sockets = []
    ports = []
    for i in range(n):
        sock = socket.socket()
        sock.bind(('', 0))
        sockets.append(sock)
    for sock in sockets:
        port = sock.getsockname()[1]
        sock.close()
        ports.append(port)
    return ports


def wait_up(url):
    """Wait for a URL to become responsive"""
    for i in range(100):
        try:
            requests.get(url)
        except requests.exceptions.ConnectionError:
            time.sleep(0.1)
        else:
            return
    raise TimeoutError("Never showed up: %s" % url)


default_config = Config()
default_config.ConfigurableHTTPProxy.api_url = 'http://127.0.0.1:%i' % tuple(
    random_ports(1)
)

from unittest.mock import MagicMock

hub = MagicMock()
hub.url = 'http://127.0.0.1'
app = MagicMock()
app.subdomain_host = ''
app.statsd_host = None


async def start_proxy(ProxyClass, port):
    """Start a proxy
    
    Returns the proxy's public and API URLs.
    """
    proxy_url = 'http://127.0.0.1:%i' % port
    proxy = ProxyClass(public_url=proxy_url, config=default_config, hub=hub, app=app)
    f = proxy.start()
    if inspect.isawaitable(f):
        await f

    def stop(*args):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        f = proxy.stop()
        if inspect.isawaitable(f):
            loop.run_until_complete(f)

    atexit.register(stop)
    wait_up(proxy_url)
    return proxy


async def add_worker(proxy, port):
    """Start a single worker.
    
    Returns the worker's URL prefix
    """
    prefix = '/worker/%i/' % port

    worker = Popen(
        [
            sys.executable,
            worker_py,
            '--port=%i' % port,
            '--prefix=%s' % prefix,
            '--logging=warn',  # info would log every request (could be lots)
        ]
    )
    atexit.register(worker.terminate)
    worker_url = 'http://127.0.0.1:%i' % port
    wait_up(worker_url)
    await proxy.add_route(prefix, worker_url, {})
    return prefix


async def bootstrap(nworkers=1):
    """Start proxy and worker

    Returns (urls, routes): the proxied URLs and the routing table.
    """
    ports = random_ports(nworkers + 1)
    proxy_port = ports.pop()
    proxy = await start_proxy(ConfigurableHTTPProxy, proxy_port)
    urls = []
    for i in range(nworkers):
        prefix = await add_worker(proxy, ports.pop())
        urls.append(proxy.public_url + prefix)
    routes = await proxy.get_all_routes()
    return urls, routes


def single_run_http(url, delay, size, msgs):
    """Time a single http request"""
    tic = time.time()
    with urlopen(url) as f:
        f.read()
    toc = time.time()
    return toc - tic


def single_run_ws(url, delay, size, msgs):
    """Time a single websocket run"""
    buf = hexlify(os.urandom(size // 2)).decode('ascii')
    msg = json.dumps({'delay': delay, 'data': buf})

    async def go():
        ws = await websocket_connect(url.replace('http', 'ws') + '/ws')
        for i in range(msgs):
            ws.write_message(msg)
            await ws.read_message()
    asyncio.set_event_loop(asyncio.new_event_loop())
    tic = time.time()
    IOLoop.clear_current()
    loop = IOLoop(make_current=True)
    loop.run_sync(lambda : go())
    toc = time.time()
    return (toc - tic) / msgs


async def do_run(urls, n, concurrent=1, delay=0, size=0, msgs=1, ws=False):
    """Do a full run.

    Returns list of timings for samples.
    """
    if ws:
        single = single_run_ws
    else:
        single = single_run_http
    with Executor(concurrent) as pool:
        url_repeats = urls * (n // len(urls))
        delays = [delay] * n
        sizes = [size] * n
        msgs = [msgs] * n
        return list(pool.map(single, url_repeats, delays, sizes, msgs))


def summarize(data, label, reverse=False, fmt='%4.f'):
    """Summarize results.

    Prints mean and a few percentiles with some formatting.
    """

    def percentile(p):
        if reverse:
            p = 100 - p
        return np.percentile(data, p)

    print(
        "{label:10} mean: {mean}, 90%: {ninety}, 50%: {fifty}, 10%: {ten}".format(
            label=label,
            mean=fmt % data.mean(),
            ninety=fmt % percentile(90),
            fifty=fmt % percentile(50),
            ten=fmt % percentile(10),
        )
    )


def report(results):
    results = np.array(results)
    milliseconds = results * 1e3
    requests_per_sec = (1. / results).astype(int)
    summarize(requests_per_sec, 'req/sec', reverse=True)
    summarize(milliseconds, 'ms', fmt='%4.1f')


async def main():
    import argparse

    parser = argparse.ArgumentParser()
    import tornado.options

    tornado.options.options.logging = 'info'
    from tornado import log

    log.enable_pretty_logging()
    parser.add_argument(
        '--ws', action='store_true', help="Run websocket test instead of http."
    )
    parser.add_argument(
        '--msgs', type=int, default=1, help="Number of messages per websocket test."
    )
    parser.add_argument(
        '--size',
        type=int,
        default=0,
        help="Size of each websocket message (or http reply).",
    )
    parser.add_argument(
        '--delay', type=float, default=0, help="Artificial delay to add."
    )
    parser.add_argument('-n', type=int, default=100, help="Number of requests to make.")
    parser.add_argument(
        '-c', type=int, default=1, help="Number of concurrent requests."
    )
    parser.add_argument(
        '-w', '--workers', type=int, default=1, help="Number of worker processes."
    )
    parser.add_argument(
        '--plot', action='store_true', help="Show a plot of the results after running."
    )
    opts = parser.parse_args()
    print(
        "Running with {} workers, {} requests ({} concurrent)".format(
            opts.workers, opts.n, opts.c
        )
    )
    urls, routes = await bootstrap(opts.workers)
    raw_urls = [route['target'] for route in routes.values()]
    args = dict(
        n=opts.n,
        concurrent=opts.c,
        delay=opts.delay,
        msgs=opts.msgs,
        size=opts.size,
        ws=opts.ws,
    )
    baseline = await do_run(raw_urls, **args)
    results = await do_run(urls, **args)
    if opts.ws:
        print(
            "Websocket test: size: %i, msgs: %i, delay: %.1f"
            % (opts.size, opts.msgs, opts.delay)
        )
    else:
        print("HTTP test: size: %i, delay: %.1f" % (opts.size, opts.delay))
    print(
        "{} workers, {} requests ({} concurrent)".format(opts.workers, opts.n, opts.c)
    )
    print("Bypassing proxy")
    report(baseline)

    print("Proxied")
    report(results)

    if opts.plot:
        import matplotlib.pyplot as plt

        plt.plot(results, label="Proxied")
        plt.plot(baseline, label="Bypassed")
        plt.ylim(0, max(results) * 1.2)
        plt.ylabel("t (sec)")
        plt.legend(loc=0)
        plt.title(
            "%i workers, %i requests (%i concurrent)" % (opts.workers, opts.n, opts.c)
        )
        plt.show()
    IOLoop.current().stop()


if __name__ == '__main__':
    loop = IOLoop.current()
    loop.run_sync(main)
