#!/usr/bin/env python3
"""Worker backend for load-testing configurable-http-proxy

Adds itself to the proxy on startup.

Supports:

    - HTTP requests to any URL, with behaviors influenced by URL params
        - delay: artificial delay before replying
        - size: reply body size (bytes)
    - WebSocket connections to any URL ending with `/ws`
        - echos JSON messages
        - if `delay` is in JSON body, a delay will be added
"""

import json
import os

from tornado import gen, web, options, ioloop
from tornado.log import app_log
from tornado.websocket import WebSocketHandler


# random_data is a dict that caches a given number of bytes
class RandomDataCache(dict):
    def get(self, nbytes):
        if nbytes not in self:
            self[nbytes] = os.urandom(nbytes)
        return self[nbytes]


random_data = RandomDataCache()


class RandomHandler(web.RequestHandler):
    """TestHandler writes bytes on HTTP"""

    @gen.coroutine
    def get(self, path):
        size = int(self.get_argument('size', 0))
        delay = float(self.get_argument('delay', 0))
        if delay:
            yield gen.sleep(delay)
        self.finish(random_data.get(size))


class EchoHandler(WebSocketHandler):
    """EchoHandler is a WebSocketHandler"""

    @gen.coroutine
    def on_message(self, message):
        message = json.loads(message)
        delay = message.get('delay', 0)
        if delay:
            yield gen.sleep(delay)
        self.write_message(message)


def main():
    options.define('port', type=int, default=8888, help="My port.")
    options.define('ip', type=int, default='127.0.0.1', help="My ip.")
    options.define('prefix', type=str, default='', help="My CHP prefix.")
    options.parse_command_line()

    opts = options.options

    app = web.Application([('.*/ws', EchoHandler), ('(.*)', RandomHandler)])
    app.listen(opts.port)
    app_log.info("Running worker at %s:%i", opts.ip, opts.port)
    ioloop.IOLoop.current().start()


if __name__ == '__main__':
    main()
