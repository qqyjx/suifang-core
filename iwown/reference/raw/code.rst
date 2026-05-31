================
    代码说明
================

------------------------
概述
------------------------
sample api是个web api程序，实现六个接口，路径

- /pb/upload            一般数据（大部分手表数据）上传
- /alarm/upload         报警数据上传
- /call_log/upload      sos通话记录/普通通话记录上传
- /deviceinfo/upload    硬件信息上传
- /status/notify        设备在线/离线状态变化通知
- /health/sleep         提供计算后的睡眠数据给设备显示

前3个接口一定要实现，后面的接口可选，根据你的需要选择是否实现。

前两个接口是一般二进制数据，其他接口接收到手表上传的是json数据, 但从http协议角度来说，所有接口都是收到表单格式
提交的数据，我们提供的sample api都是按一般二进制格式解析数据。不能忽略content-type的框架，如spring，需要通
过其他方法修改客户端请求的content-type为application/octet-stream。

代码主要包括controller/parser/calculation/protobuf四个部分

- controller        http handler, 请求处理
- parser            数据解析
- calculation       数据计算
- protobuf          自动生成的protobuf代码

手表上传/pb/upload和/alarm/upload接口的数据格式：前15个字节是deviceid，之后的数据可能是1或多个数据包，
多个数据包时是一个接一个的排列在一起。每个数据包的格式: 第1-第2字节固定数据0x44 0x54，第3-第4字节是数据包长度，
第5-第6字节是数据包crc校验码，第7-第8字节是协议编码，后面按前面定义的数据包长度的字节是数据内容，格式是protobuf。
parser部分代码就是解析这个数据包。
/call_log/upload，/deviceinfo/upload接口接收到的其实是json，用json相关库即可解析。

calculation部分是展示预处理数据，可以调用计算服务，计算睡眠/心电分析/房颤分析数据。

------------------------
Java
------------------------
用的spring boot框架

- controller 包 com.iwown.sample4GApi.controller

  - DeviceController    /pb/upload
  - AlarmController     /alarm/upload
  - CallLogController   /call_log/upload
  - DeviceInfoController /deviceinfo/upload
  - DeviceStatusController /status/notify
  - SleepController      /health/sleep
- parser 包 com.iwown.sample4GApi.service

  - HistoryDataParser    0x80协议解析
  - OldManDataParser     0x0A协议解析
  - NewAlarmDataParser   0x12协议解析
- calculation 包 com.iwown.sample4GApi.calculation

  - SleepPreprocessor   睡眠数据处理
  - EcgPreprocessor     心电数据处理
  - RriPreprocessor     房颤数据处理


------------------------
Golang
------------------------
用的echo框架

- controller

  - data_handler.go         /pb/upload
  - alarm_handler.go        /alarm/upload
  - call_log_handler.go     /call_log/upload
  - device_info_handler.go  /deviceinfo/upload
  - device_info_handler.go  /status/notify
  - data_handler.go         /health/sleep
- parser

  - history_parser.go    0x80协议解析
  - oldman_parser.go     0x0A协议解析
  - alarm_parser.go      0x12协议解析
- calculation

  - sleep_preprocessor.go   睡眠数据处理
  - ecg_preprocessor.go     心电数据处理
  - af_preprocessor.go      房颤数据处理

------------------------
Python
------------------------
用的flask框架

- controller      app.py

  - app.py     uploadPBData
  - app.py     uploadAlarmData
  - app.py     uploadCallLog
  - app.py     uploadDeviceInfo
  - app.py     deviceStatusChange
  - app.py     getSleepResult
- parser          module pbparser

  - history_parser.py    0x80协议解析
  - oldman_parser.py     0x0A协议解析
  - alarm_parser.py      0x12协议解析
- calculation     module calculation

  - sleep_preprocessor.py   睡眠数据处理
  - ecg_preprocessor.py     心电数据处理
  - af_preprocessor.py      房颤数据处理

------------------------
C#
------------------------
用的asp.net web core框架

- controller   controllers

  - DataController.cs           /pb/upload
  - AlarmController.cs          /alarm/upload
  - CallLogController.cs        /call_log/upload
  - DeviceInfoController.cs     /deviceinfo/upload
  - DeviceStatusController.cs   /status/notify
  - SleepController.cs          /health/sleep
- parser       parser

  - HistoryDataParser.cs    0x80协议解析
  - OldmanParser.cs         0x0A协议解析
  - AlarmParser.cs          0x12协议解析
- calculation  calculation

  - Sleep_Preprocessor.cs   睡眠数据处理
  - Ecg_Preprocessor.cs     心电数据处理
  - Af_Preprocessor.cs      房颤数据处理

------------------------
php
------------------------
用的lumen框架

- controller   controller

  - data_controller.php      /pb/upload
  - alarm_controller.php     /alarm/upload
  - calllog_controller.php   /call_log/upload
  - device_controller.php    /deviceinfo/upload
  - device_controller.php    /status/notify
  - data_controller.php      /health/sleep
- parser       parser

  - history_parser.php    0x80协议解析
  - oldman_parser.php     0x0A协议解析
  - alarm_parser.php      0x12协议解析
- calculation  calculation

  - sleep_preprocessor.php   睡眠数据处理
  - ecg_preprocessor.php     心电数据处理
  - af_preprocessor.php      房颤数据处理

php版本sample api解析protobuf的方式和其他版本不同，我们protobuf用的protobuf2，但因为protoc不支持生成protobuf2定义
proto文件的php代码，所以我们用python代码做protobuf解析，php调用python脚本传递数据，获取结果，python会把解析结果转成
一个json字符串。