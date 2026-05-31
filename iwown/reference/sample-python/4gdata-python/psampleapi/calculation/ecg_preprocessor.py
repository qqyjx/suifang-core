from datetime import datetime

from iutils.datetime_utils import parse_pb_datetime
import theproto.his_data_pb2
from google.protobuf import message

def PrepareEcgData(pbPayload: bytes):
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
        seq = his_data.seq
        if his_type == theproto.his_data_pb2.HisDataType.ECG_DATA:
            his_ecg = his_data.ecg
            if his_ecg is None:
                return
			#save rawDataList with data_time, later combine all ecg data
			#with the same data_time, same data_time means it's the same
			#ecg measurement
            unix_timestamp = his_ecg.time_stamp.date_time.seconds
            date_str = parse_pb_datetime(int(unix_timestamp))
            raw_data_list = his_ecg.raw_data
            count = len(raw_data_list)
            print(f"{date_str} {seq} {count}")