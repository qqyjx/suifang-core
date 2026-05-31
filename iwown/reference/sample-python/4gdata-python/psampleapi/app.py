from flask import Flask, Response
from flask import request
import struct
import json
from pbparser.history_parser import ProceedHistoryData
from pbparser.oldman_parser import ProceedOldMan
from pbparser.alarm_parser import ProceedAlarmV2
from iutils.datetime_utils import is_valid_date
from iutils.datetime_utils import get_previous_day
from calculation.sleep_preprocessor import PrepareSleepData
from calculation.ecg_preprocessor import PrepareEcgData
from calculation.af_preprocessor import PrepareRriData

app = Flask(__name__)


@app.route("/pb/upload",methods=['POST'])
def uploadPBData():
    payload = b''
    if request.content_type == 'application/x-www-form-urlencoded':
        payload = request.get_data()
    else:
        payload = request.data
    print(payload.hex())
    if len(payload) < 23:
        print('data length below 23')
        return Response(response=bytes([0x02]), status=200, mimetype="text/plain")
    device_bytes = bytearray(15)
    prefix_bytes = bytearray(2)
    len_bytes = bytearray(2)
    crc_bytes = bytearray(2)
    opt_bytes = bytearray(2)
    device_bytes[:15] = payload[:15]
    deviceid = device_bytes.decode()
    print('device: {0}'.format(deviceid))
    start_pos = 15
    while True:
        if len(payload)<start_pos+8:
            print('data length below {0}'.format(start_pos+8))
            return Response(response=bytes([0x02]), status=200, mimetype="text/plain")
        prefix_bytes[:2] = payload[start_pos:start_pos+2]
        if prefix_bytes[0] != 0x44 or prefix_bytes[1] != 0x54:
            print('invalid data header at {0}'.format(start_pos))
            return Response(response=bytes([0x03]), status=200, mimetype="text/plain")
        len_bytes[:2] = payload[start_pos+2:start_pos+4]
        length = int(len_bytes[1])*0x100 + int(len_bytes[0])
        crc_bytes[:2] = payload[start_pos+4:start_pos+6]
        opt_bytes[:2] = payload[start_pos+6:start_pos+8]
        if len(payload) < start_pos+8+length:
            print('data length below {0}'.format(start_pos+8+length))
            return Response(response=bytes([0x02]), status=200, mimetype="text/plain")
        pbPayload = payload[start_pos+8 : start_pos+8+length]
        opt = struct.unpack('<H', opt_bytes)[0]
        if opt == 0x0A:
            ProceedOldMan(pbPayload)
        elif opt == 0x80:
            ProceedHistoryData(pbPayload)
            PrepareSleepData(pbPayload)
            PrepareEcgData(pbPayload)
            PrepareRriData(pbPayload)
        start_pos += 8 + length
        if len(payload) == start_pos:
            #just read to the end and no extra byte leave
            break
    return Response(response=bytes([0x00]), status=200, mimetype="text/plain")
    
@app.route("/alarm/upload",methods=['POST'])
def uploadAlarmData():
    payload = b''
    if request.content_type == 'application/x-www-form-urlencoded':
        payload = request.get_data()
    else:
        payload = request.data
    print(payload.hex())
    if len(payload) < 23:
        print('data length below 23')
        return Response(response=bytes([0x02]), status=200, mimetype="text/plain")
    device_bytes = bytearray(15)
    prefix_bytes = bytearray(2)
    len_bytes = bytearray(2)
    crc_bytes = bytearray(2)
    opt_bytes = bytearray(2)
    device_bytes[:15] = payload[:15]
    deviceid = device_bytes.decode()
    print('device: {0}'.format(deviceid))
    start_pos = 15
    while True:
        if len(payload)<start_pos+8:
            print('data length below {0}'.format(start_pos+8))
            return Response(response=bytes([0x02]), status=200, mimetype="text/plain")
        prefix_bytes[:2] = payload[start_pos:start_pos+2]
        if prefix_bytes[0] != 0x44 or prefix_bytes[1] != 0x54:
            print('invalid data header at {0}'.format(start_pos))
            return Response(response=bytes([0x03]), status=200, mimetype="text/plain")
        len_bytes[:2] = payload[start_pos+2:start_pos+4]
        length = int(len_bytes[1])*0x100 + int(len_bytes[0])
        crc_bytes[:2] = payload[start_pos+4:start_pos+6]
        opt_bytes[:2] = payload[start_pos+6:start_pos+8]
        if len(payload) < start_pos+8+length:
            print('data length below {0}'.format(start_pos+8+length))
            return Response(response=bytes([0x02]), status=200, mimetype="text/plain")
        pbPayload = payload[start_pos+8 : start_pos+8+length]
        opt = struct.unpack('<H', opt_bytes)[0]
        if opt == 0x12:
            ProceedAlarmV2(pbPayload)
        start_pos += 8 + length
        if len(payload) == start_pos:
            #just read to the end and no extra byte leave
            break
    return Response(response=bytes([0x00]), status=200, mimetype="text/plain")

@app.route("/call_log/upload",methods=['POST'])
def uploadCallLog():
    payload = b''
    call_log = {}
    if request.content_type == 'application/x-www-form-urlencoded':
        payload = request.get_data()
    else:
        payload = request.data
    resp = {}
    try:
        call_log = json.loads(payload)
        json_string = json.dumps(call_log, indent=4)
        print('call log: '+json_string)
        resp["ReturnCode"] = 0
    except json.JSONDecodeError:
        resp["ReturnCode"] = 10002
        
    return json.dumps(resp,ensure_ascii=False)

@app.route("/deviceinfo/upload",methods=['POST'])
def uploadDeviceInfo():
    payload = b''
    device_info = {}
    if request.content_type == 'application/x-www-form-urlencoded':
        payload = request.get_data()
    else:
        payload = request.data
    resp = {}
    try:
        device_info = json.loads(payload)
        json_string = json.dumps(device_info, indent=4)
        print('device info: '+json_string)
        resp["ReturnCode"] = 0
    except json.JSONDecodeError:
        resp["ReturnCode"] = 10002
        
    return json.dumps(resp,ensure_ascii=False)

@app.route("/status/notify",methods=['POST'])
def deviceStatusChange():
    payload = b''
    notify_info = {}
    if request.content_type == 'application/x-www-form-urlencoded':
        payload = request.get_data()
    else:
        payload = request.data
    resp = {}
    try:
        notify_info = json.loads(payload)
        json_string = json.dumps(notify_info, indent=4)
        print('device status change: '+json_string)
        resp["ReturnCode"] = 0
    except json.JSONDecodeError:
        resp["ReturnCode"] = 10002
        
    return json.dumps(resp,ensure_ascii=False)

@app.route("/health/sleep", methods=['GET'])
def getSleepResult():
    resp = {}
    deviceid = request.args['deviceid']
    sleep_date = request.args['sleep_date']
    if not deviceid or not is_valid_date(sleep_date):
        resp["ReturnCode"] = 10002
        return json.dumps(resp,ensure_ascii=False)
    print(f"getSleepResult {deviceid} {sleep_date}")
    #check whether there is sleep data available with specified deviceid/sleepdata
    data_exist = True
    if data_exist:
        prev_day = get_previous_day(sleep_date)
        #the sleep data here is get by invoke algorithm api to calculate sleep result
        #see doc here : https://api8.iwown.com/iot_platform/calculation.html#id4
        sleep = {
            "deviceid": deviceid,
            "sleep_date": sleep_date,
            "start_time": f"{prev_day} 23:15:00",
            "end_time": f"{sleep_date} 07:00:00",
            "deep_sleep": 85,
            "light_sleep": 300,
            "weak_sleep": 30,
            "eyemove_sleep": 50,
            "score": 80,
            "osahs_risk": 0,
            "spo2_score": 0,
            "sleep_hr": 60
        }
        resp["Data"] = sleep
        resp["ReturnCode"] = 0
    else:
        resp["ReturnCode"] = 10404
    return json.dumps(resp,ensure_ascii=False)

if __name__ == '__main__':
    app.run(port=8098)
