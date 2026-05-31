from datetime import datetime

from iutils.datetime_utils import parse_pb_datetime
import theproto.his_data_pb2
from google.protobuf import message

def PrepareRriData(pbPayload: bytes):
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
        if his_type == theproto.his_data_pb2.HisDataType.RRI_DATA:
            his_rri = his_data.rri
            if his_rri is None:
                return
            unix_timestamp = his_rri.time_stamp.date_time.seconds
            date_str = parse_pb_datetime(int(unix_timestamp))
            raw_data_list = his_rri.raw_data
            rri_list = []

            for raw_data in raw_data_list:
                value = int(raw_data)
                f_value = (value >> 16) & 0x0000ffff
                s_value = value & 0x0000ffff
                rri_list.append(int(f_value if f_value < 0x8000 else f_value - 0x10000))
                rri_list.append(int(s_value if s_value < 0x8000 else s_value - 0x10000))
            count = len(rri_list)
            print(f"{date_str} {seq} {count}")