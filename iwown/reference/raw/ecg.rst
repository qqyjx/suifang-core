========================
长程心电数据
========================

长程心电数据也是设备对云的对接方式，获取心电分析结果则要提交原始数据到埃微后台分析。

--------------------------------
测量心电并获取心电原始数据
--------------------------------

这部分需要开发接收数据上传的http api程序，具体查看文档“获取手表数据”一节。需要先给设备发送心电上传数据的指令，设备才会
测量并上传心电数据.

数据解析在sample api ``HistoryDataParser.java`` 里的 ``parseMultiLeadsEcgData`` 方法里，现在上传的数据格式有三种，
同一设备只可能是其中一种.

---------------------------------
发送心电上传数据的指令
---------------------------------

**URL:** https://search.iwown.com/entservice2/datawe/kdinfo

**Method:** POST

**Params/json:** ::

  {
      "device_id": "860422072208452",
      "kdopt": 1,
      "kdcode": "KD175516570745711000",
      "start_time": "2026-04-28 14:00:00",
      "end_time": "2026-04-28 16:00:00",
      "upload": 0
  }

**Note:** 

-  kdopt: 1 新增; 2 修改; 3 删除;
-  kdcode: 为这次请求自定义一个唯一编号;
-  start_time: 开始时间;
-  end_time: 结束时间;
-  upload: 0 - 不上传， 1 - 上传

---------------------------------
获取心电分析结果
---------------------------------

要获取心电分析结果，首先要开发接收分析结果的 http API，分析结果会以 json 格式上传::

  {
    "date_str": "2026-05-09 18:44:00",
    "device": "860422072199909",
    "ecg_report_url": "",
    "ecg_txt": ""
  }

先提交心电分析请求，然后等待分析结果回调。

提交心电分析请求的接口::

https://api8.iwown.com/ecgsubmit/data/submit  POST
multipart/form-data

-   "file": ecg数据文件
-   "deviceid": "860422072199909"
-   "start_time": "2026-05-09 15:50:00" 数据开始时间
-   "end_time": "2026-05-09 16:00:00" 数据结束时间
-   "signed": 0: 报告不带签名，1: 报告带签名
-   "age": 用户年龄
-   "gender": 用户性别，1 - 男， 2 - 女
-   "name": 用户姓名
-   "phone": 用户电话
数据持续数据最少要一个小时，最多72小时，文件大小不超过100M

接口会验证权限，http header里面放两个字段 account，pwd，分别放账号和密码(md5加密),
deviceid需要属于account，然后account/pwd和埃微云端存储的一致

上面的两个API开发好后，把API的url提供给埃微。提交报告分析请求到获得报告结果回调，可能需要等待一段时间，一般是20-30分钟。

提交数据文件的格式，参考sample api ``MultiLeadsEcgDataFile.java``, ecgList是HistoryDataParser里
DataParse方法传入的payload保存的列表
文件前面三个字节固定是0x44, 0x54, 0x01，然后把数据列表（HisNotify）每个数据包按照：2个字节长度，数据包内容写入。
