#!/usr/bin/env python
# -*- coding: utf-8 -*-

import collections
import logging
import os
import time
import traceback

from collector import DurationCollector, RequestCollector

from hack_patch import patch_handler

import mock

import psutil

from publisher import HTTPEndPublisher, ReportedPublisher

from tornado.gen import coroutine
import tornado.ioloop
import tornado.web


CALLBACK_FREQUENCY = 100  # ms


def initialize_monitor(tornado_app,
                       pull_metrics=True,
                       support_trace=False,
                       sentry_client=None,
                       **monitor_config):
    if support_trace:
        patch_handler(tornado_app)

    if pull_metrics:
        publisher = HTTPEndPublisher(tornado_app)
    else:
        publisher = ReportedPublisher()
    monitor = Monitor(tornado_app, publisher, sentry_client, **monitor_config)

    duration_collector = DurationCollector(monitor)
    monitor.add_collector(duration_collector)
    request_collector = RequestCollector(monitor, tornado_app)
    monitor.add_collector(request_collector)

    monitor.start()
    tornado.ioloop.IOLoop.instance().set_blocking_log_threshold(1)
    return monitor


class Monitor(object):

    def __init__(
        self,
        tornado_app,
        publisher,
        sentry_client=None,
        collectors=None,
        io_loop=None,
        measure_interval=CALLBACK_FREQUENCY,
    ):
        self.tornado_app = tornado_app
        self.publisher = publisher
        self.sentry_client = sentry_client

        if collectors is None:
            self.collectors = []
        else:
            self.collectors = collectors
        self.io_loop = io_loop or tornado.ioloop.IOLoop.current()

        self.measure_callback = tornado.ioloop.PeriodicCallback(
            self._cb,
            measure_interval,
            self.io_loop,
        )

        self._ioloop_exception_patch = None
        self._ioloop_log_stack_patch = None
        self._monkey_patch_ioloop_exceptions()
        self._monkey_patch_log_stack()

        self._counters = collections.Counter()
        self._max_gauges = {}
        self._summary = collections.defaultdict(Summary)

    def add_collector(self, collector):
        self.collectors.append(collector)

    def _monkey_patch_log_stack(self):
        if self._ioloop_log_stack_patch is not None \
                or self.sentry_client is None:
            return

        _origin_log_stack = self.io_loop.log_stack

        @coroutine
        def log_stack(ioloop, signal, frame):
            _origin_log_stack(self, signal, frame)
            message = 'IOLoop blocked for {1} seconds in\n{2}'.format(
                ioloop._blocking_signal_threshold,
                ''.join(traceback.format_stack(frame)))
            try:
                yield tornado.gen.Task(self.captureMessage, message)
            except Exception as e:
                logging.error('sentry client capture block message failed.', e)

        self._ioloop_log_stack_patch = mock.patch.object(
            self.io_loop,
            'log_stack',
            log_stack
        )
        self._ioloop_log_stack_patch.start()

    def _monkey_patch_ioloop_exceptions(self):
        if self._ioloop_exception_patch is not None:
            return

        _original_handler = self.io_loop.handle_callback_exception

        def handle_callback_exception(*args, **kwargs):
            self.count('unhandled_exceptions', 1)
            _original_handler(*args, **kwargs)

        self._ioloop_exception_patch = mock.patch.object(
            self.io_loop,
            'handle_callback_exception',
            handle_callback_exception
        )
        self._ioloop_exception_patch.start()

    def __del__(self):
        self.stop()

    def _reset_ephemeral(self):
        self._max_gauges.clear()
        self._summary.clear()
        self._counters.clear()

    def count(self, stat, value=1):
        self._counters[stat] += value

    def kv(self, stat, value):
        self._summary[stat].sum += value
        self._summary[stat].count += 1

        if stat not in self._max_gauges \
                or value > self._max_gauges[stat]:
            self._max_gauges[stat] = value

    def start(self):
        for collector in self.collectors:
            collector.start()
        self.publisher.start(self)
        self._last_cb_time = time.time()
        self.measure_callback.start()

    def stop(self):
        self.publisher.stop()
        for collector in self.collectors:
            collector.stop()
        if self.measure_callback is not None:
            self.measure_callback.stop()
            self.measure_callback = None
        if self._ioloop_exception_patch is not None:
            self._ioloop_exception_patch.stop()
            self._ioloop_exception_patch = None
        if self._ioloop_log_stack_patch is not None:
            self._ioloop_log_stack_patch.stop()
            self._ioloop_log_stack_patch = None

    def _cb(self):
        now = time.time()
        latency = now - self._last_cb_time
        excess_latency = latency - (CALLBACK_FREQUENCY / 1000.0)
        self._last_cb_time = now

        self.kv('ioloop_excess_callback_latency', excess_latency)
        if hasattr(self.io_loop, '_handlers'):
            self.kv('ioloop_handlers', len(self.io_loop._handlers))
        if hasattr(self.io_loop, '_callbacks'):
            self.kv('ioloop_pending_callbacks', len(self.io_loop._callbacks))

    @property
    def metrics(self):
        ps = psutil.Process(os.getpid())
        mem_info = ps.memory_info()
        cpu_info = ps.cpu_times()
        num_fds = ps.num_fds()

        avg_gauges = {}
        for k, v in self._summary.iteritems():
            avg_gauges[k] = float(v.sum) / v.count

        rv = {
            'process': {
                'mem_info': {
                    'rss_bytes': mem_info.rss,
                    'vsz_bytes': mem_info.vms,
                },
                'cpu': {
                    'user_time': cpu_info.user,
                    'system_time': cpu_info.system,
                },
                'num_fds': num_fds
            },
            'counters': dict(self._counters),
            'max_gauges': dict(self._max_gauges),
            'avg_gauges': avg_gauges
        }
        self._reset_ephemeral()
        return rv


class Summary:
    def __init__(self, sum=0, count=0):
        self.sum = sum
        self.count = count
