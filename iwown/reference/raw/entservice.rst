================
 手表设置
================

# 云端对接方式调用的API 

这个API用来设置设备，发送指令，获取一些设备信息/状态

中国大陆地区 **Host:** https://search.iwown.com

中国港澳台及世界其他国家 **Host:** https://euapi.iwown.com

返回值格式::

    {
        "ReturnCode":0,
        "Data":{}
    }

ReturnCode:

- 0 - 代表正常
- 10001/10505 - API处理有错误
- 10002 - 参数有问题，有遗漏或参数值有问题
- 10404 - 没有数据

10001/10505/10002 通常代表客户端程序调用有问题，如果自己查不出问题，需联系API开发咨询。
Data 字段根据API不同而不同，有可能没有

如果设备未激活，访问API不会成功，需要传一个参数device_model，加到URL里面 ?device_model=xxx

------------------------
下发用户设置到设备
------------------------

**URL:** /entservice/cmd/userinfo

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "height":166,
        "weight":66,
        "gender":2,
        "age":66,
        "calibrate_walk":100,
        "calibrate_run":100,
        "wrist_circle":120,
        "hypertension":1
    }

**Note:** 

-  性别（gender）：1 男，2 女;
-  身高（height）：单位厘米;
-  体重（weight）：单位公斤;
-  年龄（age）;
-  calibrate_walk、calibrate_run 请勿修改，默认100
-  wrist_circle: 腕围，单位毫米，范围80~230 可选
-  hypertension: 高血压史 1 - 有, 2 - 没有 可选

------------------------
设备实时定位
------------------------

**URL:** /entservice/cmd/realtime/location

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605"
    }


------------------------
设备数据一键同步
------------------------

**URL:** /entservice/cmd/datasync

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605"
    }


------------------------
设备在线状态
------------------------

**URL:** /entservice/device/status

**Method:** GET

**Params:** ?device_id={0}

device status: 

- 0-OFFLINE
- 1-ONLINE
- 2-UNACTIVE
- 3-DISABLE
- 4-UNACTIVE/NOT EXIST


------------------------
设备跌倒检测开关
------------------------

**URL:** /entservice/cmd/fallcheck

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "fall_check":true
    }


------------------------
下发通讯录
------------------------

**URL:** /entservice/phonebook/sync

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "phone_book":[
            {
                "name":"小明一",
                "number":"15628457361",
                "sos":true
            },
            {
                "name":"小明二",
                "number":"18577546507",
                "sos":true
            },
            {
                "name":"小明三",
                "number":"13662783623",
                "sos":true
            },
            {
                "name":"小明四",
                "number":"18518931658",
                "sos":false
            }
        ],
        "forbid":1
    }

sos - 是否紧急联系人, 要求至少有一个号码是SOS的

forbid - 可选，1 拦截陌生来电, 2 不拦截陌生来电。不传的时候不设置陌生来电拦截

name - 最大24字节

number - 最大20字节

最多设置8个号码

------------------------
清空通讯录
------------------------

**URL:** /entservice/phonebook/clear

**Method:** POST

**Params/json:** ::

    {
        "device_id":"869595060004461"
    }

------------------------
设备数据上传间隔设置
------------------------

**URL:** /entservice/cmd/datafreq

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "gps_auto_check":true,
        "gps_interval_time":10,
        "power_mode":2
    }

gps_auto_check - 数据上传/自动定位开关

gps_interval_time - 数据上传/自动定位间隔，unit is minute

power_mode - 可选，设备省电设置：

- 1 - low耗电;手表放置和进入睡眠的时候会关闭4G模块，无法上传数据，无法接听呼入电话
- 2 - mid耗电;手表放置的时候不会关闭4G模块，可以上传数据，接听呼入电话；手表进入睡眠的时候会关闭4G模块，无法上传数据，无法接听呼入电话
- 3 - high耗电;手表放置和进入睡眠的时候，都不会关闭4G模块，数据上传和电话呼入呼出不受影响

这个接口把数据上传和自动定位用同样的设置一起设置

--------------------------------------
设备数据上传自动定位间隔设置
--------------------------------------

**URL:** /entservice/cmd/locate_dataupload/freq

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "data_auto_upload":true,
        "data_upload_interval":30,
        "auto_locate":true,
        "locate_interval_time":60,
        "power_mode":2
    }

data_auto_upload - 数据定时上传开关

data_upload_interval - 数据定时上传间隔，unit is minute

auto_locate - 自动定位开关

locate_interval_time - 定位间隔，unit is minute

power_mode - 可选，设备省电设置：

- 1 - low耗电;手表放置和进入睡眠的时候会关闭4G模块，无法上传数据，无法接听呼入电话
- 2 - mid耗电;手表放置的时候不会关闭4G模块，可以上传数据，接听呼入电话；手表进入睡眠的时候会关闭4G模块，无法上传数据，无法接听呼入电话
- 3 - high耗电;手表放置和进入睡眠的时候，都不会关闭4G模块，数据上传和电话呼入呼出不受影响


------------------------
翻腕亮屏设置
------------------------

**URL:** /entservice/cmd/lcdgesture

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "open":true,
        "start_hour":0,
        "end_hour":24
    }

open - true 打开, false 关闭

开始时间（start_hour） - 开启翻腕亮屏时间, 0 - 23

结束时间（end_hour） - 关闭翻腕亮屏时间, 0 - 24


------------------------
心率报警设置
------------------------

**URL:** /entservice/cmd/hralarm

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "open":true,
        "high":130,
        "low":50,
        "threshold":3,
        "alarm_interval":10
    }

open - true 打开心率报警, false 关闭心率报警

high - 正常心率最高值

low - 正常心率最低值

threshold - 产生几次异常心率，开始报警

alarm_interval - 报警间隔，单位分钟

------------------------
动态心率报警设置
------------------------

**URL:** /entservice/cmd/dynamic/hralarm

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "open":true,
        "high":130,
        "low":50,
        "timeout":30,
        "interval":2
    }

open - true 打开心率报警, false 关闭心率报警

high - 动态心率最高值

low - 动态心率最低值

timeout - 异常心率达到多少时长，单位秒，开始报警

interval - 报警间隔，单位分钟

------------------------
血氧报警设置
------------------------

**URL:** /entservice/cmd/spo2alarm

**Method:** POST

**Params/json:** ::

    {
        "device_id":"984612114945605",
        "open":true,
        "low":80
    }

open - true 打开血氧报警, false 关闭血氧心率报警

low - 低于此血氧值报警


------------------------
血压报警设置
------------------------

**URL:** /entservice/cmd/bpalarm

**Method:** POST

**Params/json:** ::

    {
        "device_id":"866655060067405",
        "open":true,
        "sbp_high":120,
        "sbp_below":80,
        "dbp_high":120,
        "dbp_below":80
    }

open - true 打开血压报警, false 关闭血压报警

sbp_high - 高压最高值，单位：mmhg

sbp_below - 高压最低值，单位：mmhg

dbp_high - 低压最高值，单位：mmhg

dbp_below - 低压最低值，单位：mmhg


------------------------
温度报警设置
------------------------

**URL:** /entservice/cmd/temperature/alarm

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "open":true,
        "high":380,
        "low":350
    }

温度值设置需要乘10移除小数，例如：37.5°设置375

------------------------
血糖报警设置
------------------------

**URL:** /entservice/cmd/sugaralarm

**Method:** POST

**Params/json:** ::

    {
        "device_id":"866655060067405",
        "open":true,
        "blood_sugar_low":3.5,
        "blood_sugar_high":9.5
    }

open - true 打开血糖报警, false 关闭血糖报警

blood_sugar_low - 低于这个值报警

blood_sugar_high - 高于这个值报警

------------------------
血钾报警设置
------------------------

**URL:** /entservice/cmd/potassiumalarm

**Method:** POST

**Params/json:** ::

    {
        "device_id":"866655060067405",
        "open":true,
        "blood_potassium_low":3.5,
        "blood_potassium_high":6.5
    }

open - true 打开血钾报警, false 关闭血钾报警

blood_potassium_low - 低于这个值报警

blood_potassium_high - 高于这个值报警

------------------------
自动房颤设置
------------------------

**URL:** /entservice/cmd/autoaf

**Method:** POST

**Params/json:**

::

    {
        "device_id":"984612114945605",
        "open":true,
        "interval":30,
        "rri_single_time":false,
        "rri_type":1
    }

open - true 打开自动房颤, false 关闭自动房颤

interval - 自动房颤测量持续时间，单位秒，最小取值30，最大取值120，设置超出范围则按60生效

rri_single_time - 可选值。设置为true，按照心率间隔，每次测量完心率后测量一次。设置为false，一直测量

rri_type - 可选值。默认是0， 0:普通rri, 1:计算心情用rri

------------------------
设置闹钟
------------------------

**URL:** /entservice2/clockalarm/set

**Method:** POST

**Params/json:**

::

    {
        "device_id":"869595060004461",
        "alarms":[
            {
                "repeat":true,
                "monday":true,
                "tuesday":true,
                "wednesday":true,
                "thursday":true,
                "friday":true,
                "saturday":true,
                "sunday":true,
                "hour":10,
                "minute":30,
                "title":"hehe"
            }
        ]
    }

最多设置5个闹钟

repeat - true 重复, false 一次性

monday ~ sunday 对应的那天是否打开

hour/minute: 时间 小时:分钟, 24小时

title: 闹钟内容


------------------------
清除闹钟
------------------------

**URL:** /entservice2/clockalarm/clear

**Method:** POST

**Params/json:**

::

    {
        "device_id":"869595060004461"
    }


------------------------
设置久坐
------------------------

**URL:** /entservice3/sedentary/set

**Method:** POST

**Params/json:**

::

    {
        "device_id":"869595060004461",
        "sedentaries":[
            {
                "repeat":true,
                "monday":true,
                "tuesday":true,
                "wednesday":true,
                "thursday":true,
                "friday":true,
                "saturday":true,
                "sunday":true,
                "start_hour":0,
                "end_hour":23,
                "duration":5,
                "threshold":10
            }
        ]
    }

最多设置3个久坐提醒

repeat - true 重复, false 一次性

monday ~ sunday 对应的那天是否打开

start_hour/end_hour: 久坐检查时间段，开始/结束小时 24小时

duration: 设置累计久坐分钟数达到多少分钟提醒, 单位为5分钟，即设置1，等于5分钟

threshold: 一分钟内步数少于多少，判断这一分钟为久坐，计数+1，单位步，默认40

duration/threshold: 只针对新设备新版本有效


------------------------
清除久坐
------------------------

**URL:** /entservice3/sedentary/clear

**Method:** POST

**Params/json:**

::

    {
        "device_id":"869595060004461"
    }


------------------------
目标设置
------------------------

**URL:** /entservice/cmd/goal

**Method:** POST

**Params/json:**

::

    {
        "device_id":"984612114945605",
        "step":10000,
        "distance":10000,
        "calorie":400
    }

step - 目标步数

distance - 目标距离,单位米

calorie - 目标卡路里,单位千卡


------------------------
设备恢复出厂
------------------------

**URL:** /entservice/cmd/factory/reset

**Method:** POST

**Params/json:**

::

    {
        "device_id":"984612114945605"
    }


------------------------
设置语言
------------------------

**URL:** /entservice/cmd/language/set

**Method:** POST

**Params/json:**

::

    {
        "device_id":"984612114945605",
        "language":0
    }


language:

- 0 English
- 1 Chinese
- 2 Italian
- 3 Japanese
- 4 France
- 5 German
- 6 Portuguese
- 7 Spanish
- 8 Russian
- 9 Korean
- 10 Arabic
- 11 Vietnam
- 12 Polish
- 13 Romanian
- 14 Swedish
- 15 Thai
- 16 Turkish
- 17 Denish
- 18 Ukrainian
- 19 Norwegian
- 20 Dutch
- 21 Czech
- 22 Chinese_Tc
- 23 Indonesian

------------------------
发送设备消息
------------------------

**URL:** /entservice/cmd/message

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "title":"好消息",
        "description":"中奖了，500万"
    }

title最多15个字节, description最多240个字节


------------------------
设置跌倒检测灵敏度
------------------------

**URL:** /entservice/cmd/fallcheck/sensitivity

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "fall_threshold":20000
    }

fall_threshold: 跌倒检测的灵敏度，默认14000


------------------------
逆地理编码
------------------------

**URL:** /entservice/location/geocode/regeo

**Method:** POST

**Params/json:**

::

    {
        "lon":113.88959503173828,
        "lat":22.501605987548828,
        "account":"",
        "password":""
    }

lon/lat params receive coordination system in GCJ-02

此接口根据不同账号有相应限制，具体情况请联系相关人士确定

------------------------
心率数据测量间隔设置
------------------------

**URL:** /entservice/cmd/measure/interval/hr

**Method:** POST

**Params/json:**

::

    {
        "device_id":"984612114945605",
        "interval":10
    }

interval - unit is minute, minimum 1 minute


-----------------------------
其他非心率数据测量间隔设置
-----------------------------

**URL:** /entservice/cmd/measure/interval/other

**Method:** POST

**Params/json:**

::

    {
        "device_id":"984612114945605",
        "interval":10
    }
    
非心率健康类功能,包括血氧、压力值、血压等。
interval - 间隔时间, 单位分钟，最小5分钟


------------------------
Gps定位
------------------------

**URL:** /entservice/cmd/gps/locate

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "gps_auto_check":true,
        "gps_interval_time":10,
        "run_gps":true        
    }

手表设置是否每次都进行GPS定位

gps_auto_check - 定位开关

gps_interval_time - 定位间隔，unit is minute

run_gps - 是否每次都进行GPS定位


------------------------
设置时间格式(12/24小时)
------------------------

**URL:** /entservice/cmd/timeformat

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "hour_format":1      
    }

手表设置时间显示24小时还是12小时

hour_format - 0 24小时，1 12小时

------------------------
设置日期格式
------------------------

**URL:** /entservice/cmd/dateformat

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "date_format":1      
    }

手表设置日期显示 月/日还是日/月

date_format - 0 月/日，1 日/月

------------------------
设置距离单位
------------------------

**URL:** /entservice/cmd/distanceunit

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "distance_unit":1      
    }

手表设置距离单位 公制还是英制

distance_unit - 0 公制，1 英制

------------------------
设置温度单位
------------------------

**URL:** /entservice/cmd/temperatureunit

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "temperature_unit":1      
    }

手表设置温度单位 摄氏度还是华氏度

temperature_unit - 0 摄氏度，1 华氏度

------------------------
设置佩戴手
------------------------

**URL:** /entservice/device/cmd/wearhand

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "right":false
    }

手表设置佩戴手 左手还是右手

right - false 左手，true 右手

------------------------
设置血压测量计划
------------------------

**URL:** /entservice/cmd/bp/measure/schedule

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "measure_time":[
            "2025-03-14 08:05:02",
            "2025-03-14 09:05:02",
            "2025-03-14 10:05:02"            
        ]
    }

手表设置血压测量计划，最多设置48个时间点

------------------------
设置血压校准
------------------------

**URL:** /entservice/cmd/bpadjust

**Method:** POST

**Params/json:**

::

    {
        "device_id":"866655060067405",
        "sbp_band":120,
        "dbp_band":70,
        "sbp_meter":130,
        "dbp_meter":80
    }

sbp_band - 手表测量收缩压，dbp_band - 手表测量舒张压，sbp_meter - 血压计测量收缩压，dbp_meter - 血压计测量舒张压


-----------------------------
api鉴权
-----------------------------
如果向我们申请了API鉴权，调用API给属于您的手表发指令会检查账号信息，与设备所属客户的账号一致，才能给手表下发指令，否则返回::

    {
	    "ReturnCode": 10403
    }

鉴权方法是在Http Header里面添加account和pwd字段，account填入账号，pwd填入密码的md5加密值