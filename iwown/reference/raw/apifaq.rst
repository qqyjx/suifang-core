====================
 API开发技术问题
====================


**Q:** 选择哪个sample api

**A:** 根据您自己的技术栈情况选择您熟悉语言的sample api，还有个先决条件是需要对http协议有一定程度了解，知道
http header/content-type等概念。最好对API程序服务端部署也有相关经验。


**Q:** 收到数据为全\\0

**A:** springboot框架给API增加 consumes = {MediaType.APPLICATION_FORM_URLENCODED_VALUE} 声明也无法
解析数据，您程序收到数据显示的结果会是如图

.. image:: _static/receive_zero.png


**Q:** nginx配置content-type

**A:** 假设您的API地址是http://xxx.baidu.com/4gtest/pb/upload，nginx配置文件加入如下设置

.. image:: _static/nginx.jpeg

**Q:** 为什么Java sample API要配置content-type，而其他语言不用

**A:** 需要配置content-type是由两个因素导致，1.手表上传数据设置的content-type是表单方式application/x-www-form-urlencoded
（硬件4G模块限制），但内容又不是按application/x-www-form-urlencoded（表单）提交（有二进制格式，有json格式提交），
2. spring框架收到content-type是application/x-www-form-urlencoded的请求，会按表单方式解析数据。但这时按表单方式
解析数据会报错，显示收到的数据为全\\0或大部分为\\0。设置content-type为application/octet-stream可以解决这个问题，
这时服务端API就可以按普通二进制格式来解析数据。其他语言的sample API不像spring框架一定要用表单方式解析数据，而是可以
用普通二进制格式来解析数据。java用的其他web开发框架如micronaut也可以不配置content-type，如果您能修改spring的使用方式找到
不管客户端请求的content-type，只按普通二进制格式来解析，也可以达到目的。总结：配置content-type是我们提出的一种解决方法，
还有其他解决方法，只要能做到让服务端API按照普通二进制（application/octet-stream）来处理上传数据就行了。

**Q:** Linux下如何安装protobuf python库

**A:** **1. 安装protobuf**

下载protobuf安装文件, 名字类似protobuf-{version}.tar.gz, protobuf-{version}.zip，版本需要支持Proto2，
而不是Proto3.
解压并进入解压后的目录, 执行如下命令

.. code-block:: bash

    ./configure
    make
    make install

**2. 安装protobuf python库**

进入子目录python, 执行如下命令

.. code-block:: bash

   python3 setup.py install