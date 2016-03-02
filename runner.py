#!/usr/bin/env python

import atexit
import json
import os
from pprint import pprint
import random
import socket
from subprocess import Popen
import sys
import time

from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
Executor = ThreadPoolExecutor
# from concurrent.futures import ProcessPoolExecutor
import numpy as np
import requests
from urllib.request import urlopen

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
    """Start a proxy"""
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
    prefix = '/worker/%i/' % port
    
    worker = Popen([sys.executable, worker_py,
        '--port=%i' % port,
        '--proxy=%s' % proxy_api_url,
        '--prefix=%s' % prefix,
    ])
    atexit.register(worker.terminate)
    worker_url = 'http://127.0.0.1:%i' % port
    wait_up(worker_url)
    return prefix


def get_going(nworkers=1):
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
    pprint(r.json(), sys.stderr)
    return urls, r.json()


def single_run(url, delay, size):
    # url = random.choice(urls)
    tic = time.time()
    # r = requests.get(url, dict(delay=delay, size=size))
    with urlopen(url) as f:
        f.read()
    toc = time.time()
    # r.raise_for_status()
    return toc-tic
    
def do_run(urls, n, concurrent=1, delay=0, size=0):
    with Executor(concurrent) as pool:
        # urls_s = [urls] * n
        url_repeats = urls * (n // len(urls))
        # while len(url_repeats)
        delays = [delay] * n
        sizes = [size] * n
        return list(pool.map(single_run, url_repeats, delays, sizes))

def summarize(data, label, reverse=True):
    def percentile(p):
        if reverse:
            p = 100 - p
        return np.percentile(data, p)
    
    print("{label:10} mean: {mean:4.0f}, 90%: {ninety:4.0f}, 50%: {fifty:4.0f}, 10%: {ten:4.0f}".format(
        label=label,
        mean=data.mean(),
        ninety=percentile(90),
        fifty=percentile(50),
        ten=percentile(10),
    ))

def report(results):
    results = np.array(results)
    milliseconds = results * 1e3
    requests_per_sec = (1./results).astype(int)
    summarize(requests_per_sec, 'req/sec', reverse=True)
    summarize(milliseconds, 'ms')

if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('-n', type=int, default=100, help="Number of requests to make.")
    parser.add_argument('-c', type=int, default=1, help="Number of concurrent requests.")
    parser.add_argument('-w', '--workers', type=int, default=1, help="Number of worker processes.")
    parser.add_argument('--plot', action='store_true', help="Show a plot of the results after running.")
    opts = parser.parse_args()
    print("Running with {} workers, {} requests ({} concurrent)".format(
        opts.workers, opts.n, opts.c,
    ))
    urls, routes = get_going(opts.workers)
    raw_urls = [ route['target'] for route in routes.values() ]
    baseline = do_run(raw_urls, opts.n, opts.c)
    results = do_run(urls, opts.n, opts.c)
    print("{} workers, {} requests ({} concurrent)".format(
        opts.workers, opts.n, opts.c,
    ))
    print("Bypassing proxy")
    report(baseline)
    
    print("Proxied")
    report(results)
    if opts.plot:
        print("ok")
        import matplotlib
        import matplotlib.pyplot as plt
        print(matplotlib.get_backend())
        # plt.ion()
        plt.plot(results, label="Proxied")
        plt.plot(baseline, label="Bypassed")
        plt.ylim(0, max(results) * 1.2)
        plt.ylabel("t (sec)")
        plt.legend(loc=0)
        plt.title("%i workers, %i requests (%i concurrent)" % (opts.workers, opts.n, opts.c))
        plt.show()
