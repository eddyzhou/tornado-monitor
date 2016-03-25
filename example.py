#!/usr/bin/env python
# -*- coding: utf-8 -*-


import signal

import tornado.gen
import tornado.httpserver
import tornado.ioloop
import tornado.options
from tornado.options import define, options
import tornado.web

from tornmc.client import Client

from tornmon.hack_patch import RequestContext
from tornmon.monitor import initialize_monitor


define('port', default=8888, help='run on the given port', type=int)
define('memcached_host', default='127.0.0.1:11211', help='memcached host')


class Application(tornado.web.Application):
    def __init__(self):
        handlers = [
            (r'/bar', BarHandler),
            (r'/foo/(\d+)', FooHandler),
        ]
        settings = dict(
            debug=True,
        )
        super(Application, self).__init__(handlers, **settings)

        self.mc = Client([options.memcached_host])


class BaseHandler(tornado.web.RequestHandler):

    @property
    def mc(self):
        return self.application.mc


class FooHandler(BaseHandler):

    @tornado.gen.coroutine
    def get(self, x):
        '''
        import functools
        yield tornado.stack_context.run_with_stack_context(
                tornado.stack_context.StackContext(
                    functools.partial(
                        RequestContext('span_%d' % generate_id())
                    )
                ),
                functools.partial(self.foo, 5))
        '''
        yield self.mc.set('foo', 'foo')
        foo = yield self.mc.get('foo')
        yield self.foo(x)
        self.write(foo)

    @tornado.gen.coroutine
    def foo(self, x):
        print '****** x:', x
        print RequestContext.current().value
        yield self.mc.set('foo', 'foo')
        print '***** set:', RequestContext.current().value
        yield self.mc.get('foo')
        print '***** get:', RequestContext.current().value
        yield self.bar()

    @tornado.gen.coroutine
    def bar(self):
        print '***** bar:', RequestContext.current().value
        yield self.mc.set('bar', 'bar')
        print '***** bar set:', RequestContext.current().value
        yield self.mc.get('bar')
        print '***** bar get:', RequestContext.current().value


class BarHandler(BaseHandler):

    def get(self):
        self.foo()
        self.write('bar')

    def foo(self):
        print 'BarHandler.foo get value:', RequestContext.current().value
        print '==========='


application = Application()
monitor = initialize_monitor(application, support_trace=True)


def shutdown(*args):
    monitor.stop()
    application.mc.disconnect_all()
    tornado.ioloop.IOLoop.current().stop()


def main():
    for sig in (signal.SIGQUIT, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)

    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(application)
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.current().start()


if __name__ == '__main__':
    main()
