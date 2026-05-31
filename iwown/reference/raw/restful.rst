================
 获取手表数据
================

----------------
如何开发云端API
----------------

您需要开发一个Web API程序，使用http协议。部署您的API程序后，您应该有一个API的地址（URL），这将是设备上传数据的地址。这个API接收手表上传的数据，解析后根据您的业务需求决定怎么处理数据。这个API的开发请参考我们提供的API示例程序，示例程序下载链接在下面提供。编译示例程序部署到您的服务器，即可接受手表上传数据。

----------------
API示例程序
----------------

Java版本API(springboot):
`Java示例下载 <http://api8.iwown.com/dist/4gdata-java.zip>`_

Golang版本API(echo):
`Go示例下载 <http://api8.iwown.com/dist/4gdata-golang.zip>`_

python版本API(flask):
`python示例下载 <http://api8.iwown.com/dist/4gdata-python.zip>`_

c#版本API(asp.net):
`c#示例下载 <http://api8.iwown.com/dist/4gdata-csharp.zip>`_

php版本API(lumen):
`php示例下载 <http://api8.iwown.com/dist/4gdata-php.zip>`_

API示例程序包括6个API，1. 设备上传健康数据的，2. 设备上传报警的， 3. 上传SOS报警和通话记录。
4. 设备上传设备信息 5. 平台上传设备状态变化消息 6. 供设备获取睡眠结果

设备只会调这几个API上传设备数据，这几个API的路径必须
是/pb/upload,/alarm/upload,/call_log/upload,/deviceinfo/upload,
/status/notify,/health/sleep 路径结尾，前面路径一致，如:

::

    http://xxx.dev.com/4g/pb/upload
    http://xxx.dev.com/4g/alarm/upload
    http://xxx.dev.com/4g/call_log/upload
    http://xxx.dev.com/4g/deviceinfo/upload
    http://xxx.dev.com/4g/status/notify
    http://xxx.dev.com/4g/health/sleep


前3个接口一定要实现，后面的接口可选，根据你的需要选择是否实现。``

:strong:`重要注意事项`

设备上传数据的实际mime类型是application/octet-stream，但Http Header里面设置的Content-Type是
application/x-www-form-urlencoded，所以如果您的API程序用的Web框架(如springboot)会强制检查Http Header
里面设置的Content-Type和数据的格式是否一致，那么解析http内容会出错。如果您无法修改这个逻辑，需要配置一个web服务器，
如nginx/apache，修改转发到这个API URL的请求Content-Type为application/octet-stream。以我们提供的API示例程序来说，
Java(springboot)示例程序需要配置web服务器修改Content-Type，其他版本示例程序不需要。
api开发的一些技术问题请进一步参考这个文档 `apifaq <apifaq.html>`_

----------------
上传数据格式
----------------

上传数据格式包是埃微自定义格式，具体如下

.. list-table::
   :header-rows: 1

   * - header
     - field
     - type
     - notation
   * - 
     - prefix
     - uint8[2]
     - 数据包标识，固定为0x4454
   * - 
     - length
     - uint16
     - 数据（payload）长度
   * - 
     - crc
     - uint16
     - payload的crc校验码
   * - 
     - opt
     - uint16
     - payload协议所属的数据协议编码
   * - payload
     - data
     - uint8[]
     - 数据内容，protobuf生成

opt定义您会用到的有:

- 0x80 所有健康数据
- 0x0A 当前步数/距离/卡路里；GNSS数据
- 0x12 设备警报

设备上传的数据是一个或多个数据包前后拼接构成的。数据用小端模式。

数据定义proto文件下载:

`proto文件 <http://api8.iwown.com/dist/proto.zip>`_

proto文件只是用来生成protobuf类型代码的,这里面只有少数是您会用到的，具体会用到的数据类型和含义请参考sample api数据解析部分

----------------
测试您的API
----------------

访问测试工具: `API测试 <https://search.iwown.com/connect4g>`_
测试地址请填您的/pb/upload地址，如http://xxx.dev.com/4g/pb/upload

**成功**

.. image:: _static/api_test_succeed.png
   :alt: 成功

**失败**

.. image:: _static/api_test_failed.png
   :alt: 失败

这个测试工具是根据我们的手表接受的API返回协议测试，正常情况下，API应返回0x00

------------------------
    修改手表上传数据地址
------------------------

部署好您的API后，需要修改手表上传数据地址到您的API地址，我们提供了Android app做地址修改，app通过蓝牙连接手表，写入上传地址，app下载地址:
`app <http://api8.iwown.com/dist/IWBle_debug_1.28.0920.apk>`_

app使用说明:
`app手册 <http://api8.iwown.com/dist/domain_burning.pdf>`_

app操作视频:
`app视频 <http://api8.iwown.com/dist/IMG_1298.mov>`_

------------------------
 如何给手表发送指令
------------------------

指令下发可以访问entservice服务，entservice服务参考文档- `entservice <entservice.html>`_
