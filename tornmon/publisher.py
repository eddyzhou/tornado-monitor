#!/usr/bin/env python
# -*- coding: utf-8 -*-

import logging

import net

import tornado.ioloop


class ReportedPublisher(object):

    PUBLISH_FREQUENCY = 10 * 1000  # ms

    def __init__(self, publish_interval=None):
        self.publish_callback = None
        self.publish_interval = publish_interval or self.PUBLISH_FREQUENCY

    def start(self, monitor):
        if self.publish_callback is not None:
            raise ValueError('Publish callback already started')

        self.publish_callback = tornado.ioloop.PeriodicCallback(
            lambda: self._publish(monitor),
            self.publish_interval,
            monitor.io_loop
        )
        self.publish_callback.start()

    def stop(self):
        if self.publish_callback is not None:
            self.publish_callback.stop()
            self.publish_callback = None

    def _publish(self, monitor):
        try:
            self._publish_metrics(monitor.metrics)
        except:
            logging.exception('Tornado metrics publisher raised an exception')

    def _publish_metrics(self, metrics):
        pass


class MonitorHandler(tornado.web.RequestHandler):

    def initialize(self, monitor):
        self.monitor = monitor

    def prepare(self):
        if not self.request_filter():
            self.send_error(403)

    def get(self):
        self.write(self.monitor.metrics)

    def request_filter(self):
        ip = self.request.headers['X-Real-Ip'] \
            if 'X-Real-Ip' in self.request.headers else self.request.remote_ip
        remote_ip = unicode(ip)
        if not remote_ip or net.is_local_address(remote_ip) \
                or net.is_private_address(remote_ip):
            return True
        return False


class HTTPEndPublisher(object):

    def __init__(self, app, host_limit=None):
        self.app = app

        if host_limit is None:
            self._host_limit = r'.*'
        else:
            self._host_limit = host_limit

    def start(self, monitor):
        self.app.add_handlers(self._host_limit, [
            (r'/monitor', MonitorHandler, {
                'monitor': monitor
            })
        ])

    def stop(self):
        pass
