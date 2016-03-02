#!/usr/bin/env python

import atexit
from binascii import hexlify
import json
import os
from pprint import pprint
import random
import socket
from subprocess import Popen
import sys
import time
from urllib.request import urlopen

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
Executor = ThreadPoolExecutor
# Executor = ProcessPoolExecutor

import numpy as np
import requests

from tornado.gen import coroutine
from tornado.ioloop import IOLoop
from tornado.websocket import websocket_connect

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

def start_proxy(port, api_port, proxy_args=None, proxy_cmd='configurable-http-proxy'):
    """Start a proxy
    
    Returns the proxy's public and API URLs.
    """
    if proxy_args is None:
        proxy_args = []
    cmd = [proxy_cmd]
    if proxy_args:
        cmd.extend(proxy_args)
    cmd.append('--port=%i' % port)
    cmd.append('--api-port=%i' % api_port)
    proxy = Popen(cmd, stdout=sys.stderr)
    atexit.register(proxy.terminate)
    proxy_url = 'http://127.0.0.1:%i' % port
    proxy_api_url = 'http://127.0.0.1:%i' % api_port
    wait_up(proxy_url)
    wait_up(proxy_api_url)
    return proxy_url, proxy_api_url


def add_worker(proxy_api_url, port):
    """Start a single worker.
    
    Returns the worker's URL prefix
    """
    prefix = '/worker/%i/' % port
    
    worker = Popen([sys.executable, worker_py,
        '--port=%i' % port,
        '--proxy=%s' % proxy_api_url,
        '--prefix=%s' % prefix,
        '--logging=warn', # info would log every request (could be lots)
    ])
    atexit.register(worker.terminate)
    worker_url = 'http://127.0.0.1:%i' % port
    wait_up(worker_url)
    return prefix


def bootstrap(nworkers=1):
    """Start proxy and worker
    
    Returns (urls, routes): the proxied URLs and the routing table.
    """
    ports = random_ports(nworkers)
    proxy_port = 8000 # ports.pop()
    proxy_api_port = 8001 # ports.pop()
    public_url, proxy_api_url = start_proxy(proxy_port, proxy_api_port)
    urls = []
    for i in range(nworkers):
        prefix = add_worker(proxy_api_url, ports.pop())
        urls.append(public_url + prefix)
    r = requests.get(proxy_api_url + '/api/routes')
    r.raise_for_status()
    return urls, r.json()


def single_run_http(url, delay, size, msgs):
    """Time a single http request"""
    tic = time.time()
    with urlopen(url) as f:
        f.read()
    toc = time.time()
    return toc-tic

def single_run_ws(url, delay, size, msgs):
    """Time a single websocket run"""
    buf = hexlify(os.urandom(size//2)).decode('ascii')
    msg = json.dumps({
        'delay': delay,
        'data': buf,
    })
    loop = IOLoop()
    @coroutine
    def go():
        ws = yield websocket_connect(url.replace('http', 'ws') + '/ws', loop)
        for i in range(msgs):
            ws.write_message(msg)
            yield ws.read_message()
    tic = time.time()
    loop.run_sync(go)
    toc = time.time()
    return (toc-tic) / msgs


def do_run(urls, n, concurrent=1, delay=0, size=0, msgs=1, ws=False):
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
    
    print("{label:10} mean: {mean}, 90%: {ninety}, 50%: {fifty}, 10%: {ten}".format(
        label = label,
        mean = fmt % data.mean(),
        ninety = fmt % percentile(90),
        fifty = fmt % percentile(50),
        ten = fmt % percentile(10),
    ))

def report(results):
    results = np.array(results)
    milliseconds = results * 1e3
    requests_per_sec = (1./results).astype(int)
    summarize(requests_per_sec, 'req/sec', reverse=True)
    summarize(milliseconds, 'ms', fmt='%4.1f')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--ws', action='store_true', help="Run websocket test instead of http.")
    parser.add_argument('--msgs', type=int, default=1, help="Number of messages per websocket test.")
    parser.add_argument('--size', type=int, default=0, help="Size of each websocket messaage (or http reply).")
    parser.add_argument('--delay', type=float, default=0, help="Artificial delay to add.")
    parser.add_argument('-n', type=int, default=100, help="Number of requests to make.")
    parser.add_argument('-c', type=int, default=1, help="Number of concurrent requests.")
    parser.add_argument('-w', '--workers', type=int, default=1, help="Number of worker processes.")
    parser.add_argument('--plot', action='store_true', help="Show a plot of the results after running.")
    opts = parser.parse_args()
    print("Running with {} workers, {} requests ({} concurrent)".format(
        opts.workers, opts.n, opts.c,
    ))
    urls, routes = bootstrap(opts.workers)
    raw_urls = [ route['target'] for route in routes.values() ]
    args = dict(
        n=opts.n, concurrent=opts.c, delay=opts.delay,
        msgs=opts.msgs, size=opts.size, ws=opts.ws,
    )
    baseline = do_run(raw_urls, **args)
    results = do_run(urls, **args)
    if opts.ws:
        print("Websocket test: size: %i, msgs: %i, delay: %.1f" % (opts.size, opts.msgs, opts.delay))
    else:
        print("HTTP test: size: %i, delay: %.1f" % (opts.size, opts.delay))
    print("{} workers, {} requests ({} concurrent)".format(
        opts.workers, opts.n, opts.c,
    ))
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
        plt.title("%i workers, %i requests (%i concurrent)" % (opts.workers, opts.n, opts.c))
        plt.show()
