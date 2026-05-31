from google.protobuf import message
import theproto.his_data_pb2
import theproto.his_health_data_pb2
from iutils.datetime_utils import parse_pb_datetime
import math

def ProceedHistoryData(pbPayload):
    hisNotify = theproto.his_data_pb2.HisNotification()
    try:
        hisNotify.ParseFromString(pbPayload)
    except message.DecodeError as err:
        print('parse 80 health history error')
        return
    data_field = hisNotify.WhichOneof('data')
    if data_field == 'index_table':
        his_type = hisNotify.type
        #data type 18 : TCM, needed when proceed TCM data
        if his_type == theproto.his_data_pb2.HisDataType.YYLPFE_DATA:
            index_table = hisNotify.index_table
            index_list = index_table.index
            for index in index_list:
                seconds = index.time.seconds
                index_time_str = parse_pb_datetime(seconds)
                print('index table {0}:{1} - {2}'.format(index_time_str, index.start_seq, index.end_seq))

        
    elif data_field == 'his_data':
        his_data = hisNotify.his_data
        print('seq: {0}'.format(his_data.seq))
        his_type = hisNotify.type
        if his_type == theproto.his_data_pb2.HisDataType.HEALTH_DATA:
            his_health = his_data.health
            if his_health is not None:
                parse_health_data(his_health)
        elif his_type == theproto.his_data_pb2.HisDataType.ECG_DATA:
            his_ecg = his_data.ecg
            if his_ecg is not None:
                # ecg raw data
                parse_ecg_data(his_ecg)
        elif his_type == theproto.his_data_pb2.HisDataType.RRI_DATA:
            his_rri = his_data.rri
            if his_rri is not None:
                # rri/af raw data
                parse_rri_data(his_rri)
        elif his_type == theproto.his_data_pb2.HisDataType.SPO2_DATA:
            his_spo2 = his_data.spo2
            if his_spo2 is not None:
                # continuous spo2 data
                parse_spo2_data(his_spo2)
        elif his_type == theproto.his_data_pb2.HisDataType.THIRDPARTY_DATA:
            his_third_party = his_data.ThirdParty_data
            if his_third_party is not None:
                # our watch integrates other 3rd party devices, the data
                # of those devices will be uploaded as 3rd party data
                parse_third_party_data(his_third_party)
        elif his_type == theproto.his_data_pb2.HisDataType.PPG_DATA:
            his_ppg = his_data.ppg
            if his_ppg is not None:
                # ppg data
                parse_ppg_data(his_ppg)
        elif his_type == theproto.his_data_pb2.HisDataType.ACCELEROMETER_DATA:
            his_acc = his_data.ACCelerometer_data
            if his_acc is not None:
                # gsensor data
                parse_acc_data(his_acc)
        elif his_type == theproto.his_data_pb2.HisDataType.MULTI_LEADS_ECG_DATA:
            his_leads_ecg = his_data.MultiLeadsECG
            if his_leads_ecg is not None:
                parse_leadecg_data(his_leads_ecg)
        elif his_type == theproto.his_data_pb2.HisDataType.YYLPFE_DATA:
            his_yyl_data = his_data.YYLPFE_DATA
            if his_yyl_data is not None:
                parse_yylpfe_data(his_yyl_data)                

def parse_health_data(health_data):
    seconds = health_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)
    # Step data
    if health_data.HasField("pedo_data"):
        pedo_data = health_data.pedo_data
        step = pedo_data.step
        distance = float(pedo_data.distance) * 0.1
        calorie = float(pedo_data.calorie) * 0.1
        print('----{0} step:{1},distance:{2},calorie:{3}'.format(time_str,
            step, distance, calorie))
    
    # Heart rate data
    if health_data.HasField("hr_data"):
        hr_data = health_data.hr_data
        avg_hr = hr_data.avg_bpm
        max_hr = hr_data.max_bpm
        min_hr = hr_data.min_bpm
        print('----{0} avg hr:{1},max hr:{2},min hr:{3}'.format(time_str,
			avg_hr, max_hr, min_hr))
        
    # SPO2 data
    if health_data.HasField("bxoy_data"):
        boxy_data = health_data.bxoy_data
        avg_boxy = boxy_data.agv_oxy
        max_boxy = boxy_data.max_oxy
        min_boxy = boxy_data.min_oxy
        print('----{0} avg boxy:{1},max boxy:{2},min boxy:{3}'.format(time_str,
			avg_boxy, max_boxy, min_boxy))
        
    # Blood pressure
    if health_data.HasField("bp_data"):
        bp_data = health_data.bp_data
        sbp = bp_data.sbp
        dbp = bp_data.dbp
        print('----{0} sbp:{1},dbp:{2}'.format(time_str, sbp, dbp))
        
    # HRV/pressure
    if health_data.HasField("hrv_data"):
        hrv_data = health_data.hrv_data
        fatigue = int(hrv_data.fatigue)
        if fatigue <= 0:
            fatigue = int(math.log(float(hrv_data.RMSSD)) * 20)
        print('----{0} fatigue:{1}'.format(time_str, fatigue))
        
    # Temperature
    if health_data.HasField("temperature_data"):
        tmpr_data = health_data.temperature_data
        axillary_t = float(tmpr_data.esti_arm & 0x0000ffff) / 100.0
        est_t = float((tmpr_data.esti_arm >> 16) & 0x0000ffff) / 100.0
        shell_t = float(tmpr_data.evi_body & 0x0000ffff) / 100.0
        env_t = float((tmpr_data.evi_body >> 16) & 0x0000ffff) / 100.0
        if tmpr_data.type == theproto.his_health_data_pb2.TPAMeasureType.TPA_MEASURE_TYPE_AUTO:
            print('temperature data is available')
        else:
            print('temperature measure is not finished, still calculating, not available')
        print('----{0} est_t:{1},shell_t:{2},env_t:{3},axillary_t:{4}'.format(time_str, est_t, 
                                                                         shell_t, env_t, axillary_t))

    # Sleep data (not the final sleep result)
    if health_data.HasField("sleep_data"):
        sleep_data = health_data.sleep_data
        sleep_data_list = sleep_data.sleep_data
        charge = sleep_data.charge
        shutdown = sleep_data.shut_down
        print('----{0} charge:{1},shutdown:{2},count:{3}'.format(time_str, charge,
			shutdown, len(sleep_data_list)))
        
    if health_data.HasField("bp_bpm_data"):
        bpBpmData = health_data.bp_bpm_data
        bpm = bpBpmData.bpm
        print('----{0} bp hr:{1}'.format(time_str,bpm))
        
    if health_data.HasField("bloodPotassium_data"):
        potassiumData = health_data.bloodPotassium_data
        potassium = potassiumData.bloodPotassium
        print('----{0} potassium:{1}'.format(time_str,potassium))
    
    if health_data.HasField("bioz_data"):
        biozData = health_data.bioz_data
        r = biozData.R
        x = biozData.X
        fat = biozData.fat
        bmi = biozData.bmi
        thetype = biozData.type
        print('----{0} r:{1},x:{2},fat:{3},bmi:{4},type:{5}'.format(time_str,r,x,
                                                                    fat,bmi,thetype))  
    if health_data.HasField("Blood_sugar_data"):
        sugarData = health_data.Blood_sugar_data
        blood_sugar = sugarData.Blood_sugar
        print('----{0} sugar:{1}'.format(time_str,blood_sugar))
        

def parse_ecg_data(ecg_data):
    seconds = ecg_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)

    # Get the raw ECG data list
    raw_ecg_list = ecg_data.raw_data
    print('----{0} count:{1}'.format(time_str, len(raw_ecg_list)))

def parse_rri_data(rri_data):
    seconds = rri_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)

    # Get the raw RRI data list
    raw_rri_list = rri_data.raw_data

    # Process the raw RRI data
    rri_list = []
    for raw_rri in raw_rri_list:
        value = int(raw_rri)
        f_value = (value >> 16) & 0x0000ffff
        s_value = value & 0x0000ffff
        rri_list.append(f_value)
        rri_list.append(s_value)
    print('----{0} count:{1}'.format(time_str, len(rri_list)))


def parse_spo2_data(spo2_data):
    seconds = spo2_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)

    # Get the raw SPO2 data list
    raw_spo2_list = spo2_data.spo2_data

    # Process each raw SPO2 data entry
    for raw_spo2 in raw_spo2_list:
        spo2 = (raw_spo2 >> 24) & 0xFF
        hr = (raw_spo2 >> 16) & 0xFF
        perfusion = (raw_spo2 >> 8) & 0xFF
        touch = raw_spo2 & 0xFF
        print('----{0} spo2:{1},hr:{2},perfusion:{3},touch:{4}'.format(time_str, spo2, hr, perfusion, touch))

def parse_third_party_data(third_party_data):
    if third_party_data.HasField("DataHealth"):
        third_party_health = third_party_data.DataHealth
        mac_addr = third_party_health.mac_addr
        print('----3rd party device mac address:{0}'.format(mac_addr))

        if third_party_health.HasField("bp_data"):
            bp_data = third_party_health.bp_data
            seconds = bp_data.time.date_time.seconds
            time_str = parse_pb_datetime(seconds)
            sbp = bp_data.sbp
            dbp = bp_data.dbp
            hr = bp_data.hr
            pulse = bp_data.pulse
            mode = bp_data.MODE  # Assuming MODE is an attribute
            print('----3rd party {0} sbp:{1},dbp:{2},hr:{3},pulse:{4},mode:{5}'.format(time_str,
				sbp, dbp, hr, pulse, mode))
   
        if third_party_health.HasField("Glu_data"):
            glu_data = third_party_health.Glu_data
            seconds = glu_data.time.date_time.seconds
            time_str = parse_pb_datetime(seconds)
            blood_glu = glu_data.glu
            print('----3rd party {0} blood glucose:{1}'.format(time_str, blood_glu))

        if third_party_health.HasField("scale_data"):
            scale_data = third_party_health.scale_data
            seconds = scale_data.time.date_time.seconds
            time_str = parse_pb_datetime(seconds)
            weight = scale_data.weight
            impedance = scale_data.impedance
            units = scale_data.uints  # Assuming uints is the correct attribute name
            body_fat = scale_data.body_fat_percentage
            print('----3rd party {0} weight:{1}, impedance:{2}, uints:{3}, body_fat_percentage:{4}'.format(time_str, 
                                        weight, impedance, units, body_fat))

        if third_party_health.HasField("Spo2_data"):
            spo2_data = third_party_health.Spo2_data
            seconds = spo2_data.time.date_time.seconds
            time_str = parse_pb_datetime(seconds)
            spo2 = spo2_data.spo2
            bpm = spo2_data.bpm
            pi = spo2_data.pi
            print('----3rd party {0} spo2:{1},hr:{2},pi:{3}'.format(time_str,spo2, 
				bpm, pi))

        if third_party_health.HasField("Temp_data"):
            tmpr_data = third_party_health.Temp_data
            seconds = tmpr_data.time.date_time.seconds
            time_str = parse_pb_datetime(seconds)
            tmpr = tmpr_data.body_temp
            print('----3rd party {0} temperature:{1}'.format(time_str, tmpr))

def parse_ppg_data(ppg_data):
    seconds = ppg_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)

    # Get the raw PPG data list
    raw_ppg_list = ppg_data.raw_data

    # Process the raw PPG data
    ppg_list = []
    for raw_ppg in raw_ppg_list:
        value = raw_ppg
        f_value = (value >> 16) & 0x0000ffff
        if f_value < 0:
            f_value = f_value * -1
        s_value = value & 0x0000ffff
        if s_value < 0:
            s_value = s_value * -1
        ppg_list.append(f_value)
        ppg_list.append(s_value)
        print('{0} ppg:{1},{2}'.format(time_str, f_value, s_value))

def parse_acc_data(acc_data):
    seconds = acc_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)

    # Get the accelerometer data and parse them
    x_bytes = acc_data.acc_x
    x_list = parse_bytes_string(x_bytes)

    y_bytes = acc_data.acc_y
    y_list = parse_bytes_string(y_bytes)

    z_bytes = acc_data.acc_z
    z_list = parse_bytes_string(z_bytes)

    # Log the accelerometer data
    for i in range(len(x_list)):
        print('{0} acc x:{1},y:{2},z:{3}'.format(time_str, x_list[i],
			y_list[i], z_list[i]))
        
def parse_leadecg_data(leads_ecg_data):
    seconds = leads_ecg_data.time_stamp.date_time.seconds
    time_str = parse_pb_datetime(seconds)
    channel = leads_ecg_data.Number_of_channels
    single_channel_len = leads_ecg_data.Single_data_byte_len
    print('{0} channel_num x:{1},single_data_len:{2}'.format(time_str, channel,
			single_channel_len))

    buffer = leads_ecg_data.raw_data
    unit_size = single_channel_len * channel
    for i in range(unit_size, len(buffer) + 1, unit_size):
        for j in range(channel):
            num = 0
            for k in range(single_channel_len):
                pos = i - single_channel_len * (channel - j) + k
                offset = 8 * (single_channel_len - k - 1)
                part = buffer[pos] << offset
                num += part
            print('channel {0}, val:{1}'.format(j+1,num))

def parse_yylpfe_data(his_yyl_data):
    data_ts = his_yyl_data.time_stamp.date_time.seconds
    data_bytes = his_yyl_data.raw_data
    for i in range(0, len(data_bytes) - 11, 12):
        offset_ts = 0
        area_up = 0
        area_down = 0
        rri = 0
        motion = 0
        step = 0
        for k in range(4):
            offset_ts |= (data_bytes[i + step] & 0xFFFFFFFF) << (8 * k)
            step += 1
        for k in range(2):
            area_up |= (data_bytes[i + step] & 0xFFFF) << (8 * k)
            step += 1
        for k in range(2):
            area_down |= (data_bytes[i + step] & 0xFFFF) << (8 * k)
            step += 1
        for k in range(2):
            rri |= (data_bytes[i + step] & 0xFFFF) << (8 * k)
            step += 1
        for k in range(2):
            motion |= (data_bytes[i + step] & 0xFFFF) << (8 * k)
            step += 1
        rtc_ts = data_ts + offset_ts // 1000
        utc_ts = rtc_ts - (8 * 3600)
        print('ts:{0}, area up:{1}, area down:{2}, rri:{3}, motion:{4}'.format(utc_ts,
                                                    area_up,area_down,rri,motion))
def parse_bytes_string(bytes_arr):
    vlist = []
    for i in range(1, len(bytes_arr), 2):
        low = bytes_arr[i - 1]
        high = bytes_arr[i] << 8
        real_val = low + high
        vlist.append(real_val)
    return vlist