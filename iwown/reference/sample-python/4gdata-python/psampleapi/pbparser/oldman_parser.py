from theproto.om0_command_pb2 import OM0Report
from iutils.datetime_utils import parse_pb_datetime
from google.protobuf import message


INT32_MAX = 2147483647

def ProceedOldMan(pbPayload):
    omInfo = OM0Report()
    try:
        omInfo.ParseFromString(pbPayload)
    except message.DecodeError as err:
        print('parse oldman error')
        return
    seconds = omInfo.date_time.date_time.seconds
    #data time
    rt_time_str = parse_pb_datetime(seconds)
    
    #battery and rssi
    if omInfo.HasField("battery"):
        battery = omInfo.battery.level
        print('----{0} battery:{1}'.format(rt_time_str, battery))
    if omInfo.HasField("rssi"):
        rssi = omInfo.rssi
        if rssi > INT32_MAX:
            rssi = -((rssi ^ 0xFFFFFFFF) + 1)
        print('----{0} rssi:{1}'.format(rt_time_str, rssi))
    
    if omInfo.HasField("health"):
        rtHealth = omInfo.health
        distance = float(rtHealth.distance)*0.1
        calorie = float(rtHealth.calorie)*0.1
        step = rtHealth.steps
        #step/distance/calorie
        print('----{0} step:{1}, distance:{2}, calorie:{3}'.format(rt_time_str,step,distance,calorie))
    
    #gnss location
	#*notice: the location upload by device is in WGS_84 coordinate system, not GCJ_02
    if omInfo.track_data:
        track_list = omInfo.track_data
        for track in track_list:
            seconds = track.time.date_time.seconds
            gnss_time_str = parse_pb_datetime(seconds)
            print('----gnss time:{0},lon:{1},lat:{2},gps type{3}'.format(gnss_time_str,track.gnss.longitude,
                            track.gnss.latitude,track.gps_type))