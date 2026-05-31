================
    数据说明
================
本文档按照协议说明所有手表会上传的数据。protobuf协议里所有的时间类型，其时间戳按UTC时区（时区0）表示得到的时间即为
手表实际所处时区的时间

------------------------
protobuf - 0x0A协议
------------------------

- OM0Report
    - date_time 当次实时数据测量的时间
    - battery 实时电量
    - rssi 实时信号 
        信号有可能是负值，有的语言它的protobuf定义类型是uint32，需要做处理，sample api代码里面有处理方法 
    - health
        - step      当天实时步数
        - distance  当天实时距离，单位米
            获取数据除以10才是真正的数据,sample api里面已处理
        - calorie   当天实时消耗卡路里，单位卡
            获取数据除以10才是真正的数据,sample api里面已处理
    - track_data    地理定位数据  手表每次上传最近的5条定位数据
        - time 定位时间
        - gnss
            - longitude 经度
            - latitude  纬度
        - gps_type  定位类型
            1 基站定位；2 Wi-Fi定位；3 GPS定位

------------------------
protobuf - 0x80协议
------------------------
该协议里的数据均是数据测量时那一分钟内的数据，即数据均为一分钟的值。

- HisData
    - health
        - time_stamp 数据测量时间
        - pedo_data
            - type  运动类型
            - state    状态
            - calorie  卡路里 单位卡
                获取数据除以10才是真正的数据,sample api里面已处理
            - step     步数
            - distance 距离 单位米
                获取数据除以10才是真正的数据,sample api里面已处理
        - hr_data
            - min_bpm  最小心率
            - max_bpm  最大心率
            - avg_bpm  平均心率
        - hrv_data
            - fatigue 疲劳度
                压力值 = 100-fatigue
        - bp_data
            - sbp 收缩压
            - dbp 舒张压
        - bxoy_data
            - min_oxy 最小血氧
            - max_oxy 最大血氧
            - agv_oxy 平均血氧
        - temperature_data
            - evi_body
            - esti_arm 高位的两个字节是体温，sample api里面已处理
            - type 
                - 为1时温度可以使用，算法已完成，未完成。此时手表出值表显示数字固定不动
                - 为0时温度不可用，算法正在计算中，未完成。此时手表出值表显示数字闪动
        - sleep_data
            这里不是最终睡眠结果，是用来计算睡眠的原始数据的一部分
        - bp_bpm_data
            - bpm 血压心率
        - blood potassium
            - potassium 血钾
        - bioz 体脂率
            - r
            - x
            - bmi
            - fat
            - type
        - blood sugar
            - sugar 血糖
        - uric acid
            - uric acid 尿酸
        - matress temperature/humidity  床垫温湿度
            - temperature 温度
            - humidity 湿度            
    - ecg
        - time_stamp 数据测量时间
        - raw_data 心电数据
    - rri
        - time_stamp 数据测量时间
        - raw_data 房颤数据
    - spo2
        - time_stamp 数据测量时间
        - spo2_data 连续血氧数据
    - ThirdParty_data
        - mac_addr 第三方设备mac地址
        - bp_data 血压数据
            - sbp 收缩压
            - dbp 舒张压
            - hr
            - pulse
            - time 数据测量时间
        - scale_data 体脂秤数据
            - weight 体重
            - impedance 阻抗
            - uints
            - body_fat_percentage
            - time 数据测量时间
        - Spo2_data 血氧数据
            - bpm
            - spo2 血氧值
            - pi
            - time 数据测量时间
        - Temp_data 体温数据
            - body_temp 体温值
            - time 数据测量时间
        - Glu_data  血糖数据
            - glu 血糖值
            - time 数据测量时间
        - BloodKetones_data  血酮数据
            - BloodKetones 血酮值
            - time 数据测量时间
        - UricAcid_data      尿酸数据
            - UricAcid 尿酸值
            - time  数据测量时间
    - ppg
        - time_stamp 数据测量时间
        - raw_data ppg原始数据
    - ACCelerometer_data
        sample api里面已处理
        - time_stamp 数据测量时间
        - acc_data_count
        - acc_x  x轴
        - acc_y  y轴
        - acc_z  z轴
    - MultiLeadsECG
        sample api里面已处理
        - time_stamp
        - Number_of_channels
        - Single_data_byte_len
        - raw_data
    - ThirdParty_Data_v2
        - health_summary_time 数据测量时间
        - serial_number ppg原始数据
        - bed   在床离床状态
        - heartrate 心率
        - breathrate 呼吸率
        - motion     体动   

------------------------
protobuf - 0x12协议
------------------------

- Alarm_infokConfirm
    - Alarminfo
        - time_stamp 警报生成时间
        - wearstate  未佩戴
        - lowpowerPercentage  低电量警报生成时的电量
        - poweroffPercentage  关机警报生成时的电量
        - intercept_number    陌生号码拦截警报生成时拦截的号码
    - alarm
        - alarm_hr 心率报警，多条
            - time_stamp 警报生成时间
            - hr 心率
        - alarm_spo2 血氧报警，多条
            - time_stamp 警报生成时间
            - spo2 血氧
        - alarm_Thrombus 心血管报警，多条
            - time_stamp 警报生成时间
            - Thrombus_alarm
        - alarm_fall 跌倒报警，多条
            - time_stamp 警报生成时间
            - fall_alarm 
        - alarm_Temperature 体温报警，多条
            - time_stamp 警报生成时间
            - temperature 体温
        - alarm_Bp 血压报警，多条
            - time_stamp 警报生成时间
            - sbp
            - dbp
        - alarm_Sedentary 久坐报警，多条
            - time_stamp 警报生成时间
        - SOS_Notification_time 单条sos报警，警报生成时间,没有经纬度和通话日志
        - alarm_Blood_sugar 血糖报警，多条
            - time_stamp 警报生成时间
            - Blood_sugar 血糖值
        - alarm_Blood_potassium 血钾报警，多条
            - time_stamp 警报生成时间
            - Blood_potassium 血钾值


------------------------
sos and call log
------------------------
上传数据格式::

    {
        "deviceid":"966655060102203",
        "normal_call_logs":[
            {
                "status":2,
                "call_number":"13312345678",
                "start_time":"2024-02-01 13:22:45", 
                "end_time":"2024-02-01 13:23:45"
            },
            {
                "status":3,
                "call_number":"010-87551545",
                "start_time":"2024-02-02 13:22:45",
                "end_time":"2024-02-02 13:23:45"
            }
        ],
        "sos":[
            {
                "alarm_time":"2024-02-01 13:28:45",
                "lat":"23.2",
                "lon":"145.8",
                "call_logs":[
                    {
                        "status":2, 
                        "call_number":"13312345678",
                        "start_time":"2024-02-01 13:22:45", 
                        "end_time":"2024-02-01 13:23:45"
                    }
                ]
            },
            {
                "alarm_time":"2024-02-01 13:29:45",
                "lat":"23.2",
                "lon":"145.8",
                "call_logs":[
                    {
                        "status":1,
                        "call_number":"15912345671",
                        "start_time":"2024-02-02 13:22:45",
                        "end_time":"2024-02-01 13:23:45"
                    },
                    {
                        "status":1,
                        "call_number":"15912345672",
                        "start_time":"2024-02-02 13:23:45",
                        "end_time":"2024-02-02 13:24:45"
                    },
                    {
                        "status":2, 
                        "call_number":"13312345673",
                        "start_time":"2024-02-01 13:24:45", 
                        "end_time":"2024-02-01 13:25:45"
                    }
                ]
            }
        ]
    }

- deviceid          设备编号
- normal_call_logs  通话日志
    - status        通话状态
        1 语音留言  2 接通  3 未接通
    - call_number  通话号码
    - start_time   通话开始时间
    - end_time     通话结束时间
- sos               sos报警
    - alarm_time    sos报警时间
    - lat   sos时的定位 纬度
    - lon   sos时的定位 经度
    - call_logs  sos通话日志
        - status
        - call_number
        - start_time
        - end_time

------------------------
device info
------------------------
上传数据格式::

    {
        "deviceid": "860132060872223",
        "imsi": "460016757635120",
        "sn": "",
        "mac": "50:c0:f0:0c:87:01",
        "net_type": "LTE",
        "net_operator": "460|01",
        "wearing_status": "0",
        "model": "H102CN",
        "version": "54.2.0.6",
        "sim1_iccid": "89860123801275636995",
        "sim1_cellid": "199448e",
        "band_detail": "1|5|5",
        "refsignal": "-84|-3|-79|24",
        "communication_mode": "LTE|FDD",
        "band": "LTE BAND 1",
        "watch_event": 1
    }

- net_type 网络制式
- model    设备型号
- version  设备版本
- watch_event 1 正常上传 2 开机后上传 3 恢复出厂后上传

------------------------
device status notify
------------------------
上传数据格式::

    {
        "DeviceId": "869595061621362",
        "EventTime": "2024-10-23 05:23:30",
        "Status": "online"
    }

------------------------
sleep result
------------------------
提供计算后的睡眠结果给设备显示，参数：
deviceid 
sleep_date

返回数据格式::

    {
        "ReturnCode": 0,
        "Data": {
            "deviceid": "860132061275301",
            "sleep_date": "2024-12-13",
            "start_time": "2024-12-12 23:15:00",
            "end_time": "2024-12-13 07:00:00",
            "deep_sleep": 85,
            "light_sleep": 300,
            "weak_sleep": 30,
            "eyemove_sleep": 50,
            "score": 80,
            "osahs_risk": 0,
            "spo2_score": 0,
            "sleep_hr": 60
        }
    }