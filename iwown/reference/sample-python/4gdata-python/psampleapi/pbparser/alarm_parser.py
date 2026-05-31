import theproto.Alarm_info_pb2
from google.protobuf import message
from iutils.datetime_utils import parse_pb_datetime

def ProceedAlarmV2(pbPayload):
    alarmConfirm = theproto.Alarm_info_pb2.Alarm_infokConfirm()
    try:
        alarmConfirm.ParseFromString(pbPayload)
    except message.DecodeError as err:
        print('parse alarm v2 error')
        return
    if alarmConfirm.HasField("alarm"):
        pbAlarm1 = alarmConfirm.alarm
        if pbAlarm1.alarm_hr:
            hr_alarm_list = pbAlarm1.alarm_hr
            for hr_alarm in hr_alarm_list:
                seconds = hr_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                hr = hr_alarm.hr
                print('----{0} hr alarm, hr:{1}'.format(time_str,hr))
        if pbAlarm1.alarm_spo2:
            spo2_alarm_list = pbAlarm1.alarm_spo2
            for spo2_alarm in spo2_alarm_list:
                seconds = spo2_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                spo2 = spo2_alarm.spo2
                print('----{0} spo2 alarm, boxy:{1}'.format(time_str,spo2))
        if pbAlarm1.alarm_Thrombus:
            thrombus_alarm_list = pbAlarm1.alarm_Thrombus
            for thrombus_alarm in thrombus_alarm_list:
                seconds = thrombus_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                print('----{0} thrombus alarm'.format(time_str))
        if pbAlarm1.alarm_fall:
            fall_alarm_list = pbAlarm1.alarm_fall
            for fall_alarm in fall_alarm_list:
                seconds = fall_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                print('----{0} fall alarm'.format(time_str))
        if pbAlarm1.alarm_Temperature:
            tmpr_alarm_list = pbAlarm1.alarm_Temperature
            for tmpr_alarm in tmpr_alarm_list:
                seconds = tmpr_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                tmpr = tmpr_alarm.temperature
                print('----{0} temperature alarm, temperature:{1}'.format(time_str,tmpr))                
        if pbAlarm1.alarm_Bp:
            bp_alarm_list = pbAlarm1.alarm_Bp
            for bp_alarm in bp_alarm_list:
                seconds = bp_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                sbp = bp_alarm.sbp
                dbp = bp_alarm.dbp
                print('----{0} blood pressure alarm, sbp:{1},dbp:{2}'.format(time_str,sbp,dbp))  
        if pbAlarm1.HasField("SOS_Notification_time"):
            sos_alarm_time = pbAlarm1.SOS_Notification_time
            seconds = sos_alarm_time.date_time.seconds
            time_str = parse_pb_datetime(seconds)
            print('----sos alarm time {0}'.format(time_str))
        if pbAlarm1.alarm_Blood_sugar:
            sugar_alarm_list = pbAlarm1.alarm_Blood_sugar
            for sugar_alarm in sugar_alarm_list:
                seconds = sugar_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                blood_sugar = sugar_alarm.Blood_sugar
                print('----{0} blood sugar alarm, blood sugar:{1}'.format(time_str,blood_sugar))
        if pbAlarm1.alarm_Blood_potassium:
            potassium_alarm_list = pbAlarm1.alarm_Blood_potassium
            for potassium_alarm in potassium_alarm_list:
                seconds = potassium_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                potassium = potassium_alarm.Blood_potassium
                print('----{0} blood potassium alarm, blood potassium:{1}'.format(time_str,potassium)) 
        if pbAlarm1.gnssinfo:
            gnss_alarm_list = pbAlarm1.gnssinfo
            for gnss_alarm in gnss_alarm_list:
                seconds = gnss_alarm.time_stamp.date_time.seconds
                time_str = parse_pb_datetime(seconds)
                lon = gnss_alarm.longitude
                lat = gnss_alarm.latitude
                print('----{0} gnss alarm, lon:{1},lat:{2}'.format(time_str,lon,lat))

    if alarmConfirm.HasField("Alarminfo"):
        pbAlarm2 = alarmConfirm.Alarminfo 
        seconds = pbAlarm2.time_stamp.date_time.seconds
        time_str = parse_pb_datetime(seconds)
        if pbAlarm2.HasField("wearstate"):
            print('----{0} not wear alarm'.format(time_str))
        if pbAlarm2.HasField("lowpowerPercentage"):
            power = pbAlarm2.lowpowerPercentage
            print('----{0} low power alarm, battery:{1}'.format(time_str,power))
        if pbAlarm2.HasField("poweroffPercentage"):
            power = pbAlarm2.poweroffPercentage
            print('----{0} power off alarm, battery:{1}'.format(time_str,power))
        if pbAlarm2.HasField("intercept_number"):
            number = pbAlarm2.intercept_number
            print('----{0} phone intercept alarm, phone number:{1}'.format(time_str,number))                                               