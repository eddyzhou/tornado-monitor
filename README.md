# tornado-monitor

**tonado-monitor**主要用于以下几个方面:

- 监控tornado的运行(比如callback延时，内存、cpu占用等)
- 增加`/monitor`接口，方便prometheus等监控系统pull收集的metric，或者通过ReportedPublisher周期性自动上报
- 通过对handler进行hack(通过metaclass生成新的handler)，移入RequestContext，方便写入tracing数据(配合在公共库，如tornado-memcached写入tracing数据，thrift rpc传递span_id等，尽量做到对业务透明)；收集handler执行耗时，异常等数据

# Usage

例子：

```
application = Application()
monitor = initialize_monitor(application, support_track=True)


def shutdown(*args):
    monitor.stop()
    tornado.ioloop.IOLoop.current().stop()


def main():
    for sig in (signal.SIGQUIT, signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, shutdown)

    tornado.options.parse_command_line()
    http_server = tornado.httpserver.HTTPServer(application)
    http_server.listen(options.port)
    tornado.ioloop.IOLoop.current().start()
```

会自动增加`/monitor`接口，提供收集的metric的统计数据
