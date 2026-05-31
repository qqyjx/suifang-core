import json
import math
import time
from datetime import datetime

from iutils.datetime_utils import parse_pb_datetime
import theproto.his_data_pb2
from google.protobuf import message

def PrepareSleepData(pbPayload: bytes):
    hisNotify = theproto.his_data_pb2.HisNotification()
    try:
        hisNotify.ParseFromString(pbPayload)
    except message.DecodeError as err:
        print('parse 80 health history error')
        return
    data_field = hisNotify.WhichOneof('data')

    if data_field == 'his_data':
        his_data = hisNotify.his_data
        his_type = hisNotify.type
        if his_type == theproto.his_data_pb2.HisDataType.HEALTH_DATA:
            health_data = his_data.health
            if health_data is None:
                return
            unix_timestamp = health_data.time_stamp.date_time.seconds
            date_str = parse_pb_datetime(int(unix_timestamp))
            seq = his_data.seq
            sleep_dict = {"Q": seq}

            tm = time.gmtime(unix_timestamp)
            t_arr = [tm.tm_hour, tm.tm_min]
            sleep_dict["T"] = t_arr

            pedo = health_data.pedo_data
            if pedo:
                detail_dict = {
                    "s": pedo.step,
                    "c": pedo.calorie,
                    "d": pedo.distance,
                    "t": pedo.type,
                    "a": pedo.state & 15
                }
                sleep_dict["P"] = detail_dict

            hr = health_data.hr_data
            if hr:
                detail_dict = {}
                if hr.avg_bpm > 0:
                    detail_dict["a"] = hr.avg_bpm
                if hr.max_bpm > 0:
                    detail_dict["x"] = hr.max_bpm
                if hr.min_bpm > 0:
                    detail_dict["n"] = hr.min_bpm
                sleep_dict["H"] = detail_dict

            hrv = health_data.hrv_data
            if hrv:
                detail_dict = {}
                if hrv.SDNN > 0:
                    detail_dict["s"] = hrv.SDNN / 10.0
                if hrv.RMSSD > 0:
                    detail_dict["r"] = hrv.RMSSD / 10.0
                if hrv.PNN50 > 0:
                    detail_dict["p"] = hrv.PNN50 / 10.0
                if hrv.MEAN > 0:
                    detail_dict["m"] = hrv.MEAN / 10.0
                fatigue = int(hrv.fatigue)
                if fatigue > -1000:
                    if fatigue <= 0 and hrv.RMSSD > 0:
                        fatigue = int(math.log(hrv.RMSSD) * 20)
                    if fatigue > 0:
                        detail_dict["f"] = fatigue
                    if detail_dict:
                        sleep_dict["V"] = detail_dict

            sleep = health_data.sleep_data
            if sleep:
                detail_dict = {
                    "a": list(sleep.sleep_data)
                }
                if sleep.shut_down:
                    detail_dict["s"] = sleep.shut_down
                if sleep.charge:
                    detail_dict["c"] = sleep.charge
                sleep_dict["E"] = detail_dict

            sleep_str = ""
            try:
                sleep_str = json.dumps(sleep_dict)
            except Exception as err:
                print(err)
                return
            # Save sleep_str with date_str/seq, later to do sleep calculation,
            # need to combine all day's sleep_str
            print(f"{date_str} {seq} {sleep_str}")


#Combine all day's sleep_str to one single string, in format json array.
#sleepDataList should order by datetime
def combine_whole_day_sleep_data(sleep_data_list):
    if not sleep_data_list:
        return "[]"
    
    return "[" + ",".join(sleep_data_list) + "]"