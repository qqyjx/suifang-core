//把HashDecryption.js和这个类一起加密打包。***打包加密
//后续要把 BluetoothDevice BleScanMessage BluetoothOption单独放一个类，单独打成一个库

/*****************************************设备广播包**********************************************/

import { ab2hex, hexDataCovetToAddress } from "../../jl_lib/jl-rcsp/jl_rcsp_watch_1.1.0";
import { decryption } from "../../jl_lib/jl_hashDecryption_1.0.0";
export class ScanDevice {
    deviceId: string
    device: BluetoothDevice
    isSystem: boolean
    constructor(deviceId: string, device: BluetoothDevice, isSystem: boolean) {
        this.deviceId = deviceId
        this.device = device
        this.isSystem = isSystem
    }
}
export class BluetoothDevice {
	/** 当前蓝牙设备的信号强度，单位 dBm */
	RSSI: number = 0
	/** 当前蓝牙设备的广播数据段中的 ManufacturerData 数据段。 */
	advertisData?: ArrayBuffer
	/** 当前蓝牙设备的广播数据段中的 ServiceUUIDs 数据段 */
	advertisServiceUUIDs?: string[]
	/** 当前蓝牙设备是否可连接（ Android 8.0 以下不支持返回该值 ） */
	connectable: boolean = true
	/** 蓝牙设备 id */
	deviceId: string = ""
	/** 当前蓝牙设备的广播数据段中的 LocalName 数据段 */
	localName: string = ""
	/** 蓝牙设备名称，某些设备可能没有 */
	name?: string
	/** 当前蓝牙设备的广播数据段中的 ServiceData 数据段 */
	serviceData: any
	public equals(o: BluetoothDevice | null): boolean {
		if (o == null) return false;
		if (this == o) return true;
		return this.deviceId == o.deviceId;
	}
}
export class BleScanMessage {
    /**
    * 原始数据
    */
    rawData?: Uint8Array;
    /**
     * 过滤标识
     */
    flagContent?: string; //过滤规则标志
    /* ------------------------------------------------------------------- *
     *  白名单参数(暂时无效)
     * ------------------------------------------------------------------- */
    pairedFlag?: number; //设备配对标志
    phoneVirtualAddress?: Uint8Array; //配对手机的虚拟地址
    /* ------------------------------------------------------------------- *
     * 固定头字段
     * ------------------------------------------------------------------- */
    /**
     * 厂商ID
     */
    pid: number = 0;
    /**
     * 产品ID
     */
    vid: number = 0;
    /**
     * 客户ID
     */
    uid: number = 0;
    /**
     * 设备类型
     * 可选值:
     * {@link -1} --- NONE
     * {@link 0} --- 音箱类型
     * {@link 1} --- 充电仓类型
     * {@link 2} --- TWS耳机-BR22系列
     * {@link 3} --- TWS耳机-BR23系列
     * {@link 4} --- 声卡类型
     * {@link 5} --- 手表类型
     */
    deviceType: number = -1;
    /**
     * 协议版本
     */
    version: number = 0;
    /**
     * 弹窗标志
     * <p>
     * 说明: 1 - true 0 - false
     * 描述: AC692X之前的广播包的属性
     * </p>
     */
    showDialog: boolean = false;
    /**
     * 设备的经典蓝牙地址
     */
    edrAddr?: string;
    /**
     * 经典蓝牙连接状态
     * <p>
     * 说明：1 -- 已连接 0 -- 未连接
     * 描述：AC692X之前的广播包的属性
     * </p>
     */
    edrStatus: number = 0;

    /* ------------------------------------------------------------------- *
     * TWS设备参数
     * ------------------------------------------------------------------- */
    /**
     * 左设备（设备1）电量
     * <p>
     * 说明：取值范围 0-100 (0 视为设备不在线)
     * </p>
     */
    leftDeviceQuantity: number = 0;
    /**
     * 左设备（设备1）充电标识
     */
    isLeftCharging: boolean = false;
    /**
     * 右设备（设备2）电量
     * <p>
     * 说明：取值范围 0-100 (0 视为设备不在线)
     * </p>
     */
    rightDeviceQuantity: number = 0;
    /**
     * 右设备（设备2）充电标识
     */
    isRightCharging: boolean = false;
    /**
     * 充电仓（设备3）电量
     * <p>
     * 说明：取值范围 0-100 (0 视为设备不在线)
     * </p>
     */
    chargingBinQuantity: number = 0;
    /**
     * 充电仓（设备3）充电标识
     */
    isDeviceCharging: boolean = false;
    /**
     * TWS配对标识
     * <p>
     * 0 -- 未配对
     * 1 -- 已配对
     * </p>
     */
    twsFlag: number = 0;
    /**
     * 充电仓状态
     * <p>
     * 0 --- 关盖
     * 1 --- 开盖
     * </p>
     */
    chargingBinStatus: number = 0;
    /**
     * 主从标识
     * <p>
     * 0 --- 从机
     * 1 --- 主机
     * </p>
     */
    mainDevFlag: number = 0;

    /**
     * 行为/状态
     * <p>
     * 说明: 表示耳机状态
     * 可选值：{@link 0} 不显示弹窗,
     * {@link 1} 蓝牙未连接,
     * {@link 2} 蓝牙已连接,
     * {@link 3} 蓝牙正在连接
     */
    action: number = 0;
    /**
     * 序列号
     * <p>
     * 说明:标识当前广播的序号，用于判断广播是否被过滤或过时
     * </p>
     */
    seq: number = 0;
    /**
     * 充电仓模式
     * <p>
     * 0 --- 充电模式
     * 1 --- 发射模式
     * </p>
     */
    chargingBinMode: number = 0;
    /* ------------------------------------------------------------------- *
     * hash加密参数
     * ------------------------------------------------------------------- */
    /**
     * 哈希值
     */
    hash?: Uint8Array;
    /* ------------------------------------------------------------------- *
     * 设备基本信息
     * ------------------------------------------------------------------- */
    /**
     * 接收信号强度
     */
    rssi?: number;
    /**
     * 是否允许连接
     * <p>说明: 默认允许连接，特殊情况不允许</p>
     */
    isEnableConnect: boolean = true;

    /**
     * 连接方式
     * <p>
     * 说明：0 -- BLE , 1 -- SPP
     * </p>
     */
    connectWay?: number;
    /**
     * 是不是OTA广播包
     */
    isOTA: boolean = false;
    /**
     * OTA广播包版本号
     */
    otaADVVersion: number = 0;
    /**
     * OTA广播包-原BLE地址
     */
    otaBleAddress?: string;
    /**
     * OTA广播包-保留位数据
     */
    otaADVReserve?: Uint8Array;
}

export class BluetoothOption {

    /*=======================================================================*
 * 通用配置
 *=======================================================================*/

    /**
     * 是否允许自动重连
     * 说明:异常断开重连机制
     */
    reconnect: boolean = false
    /**
     * 命令超时时间
     */
    timeoutMs = 2000

    /**
     * 是否进入低功耗模式
     * 说明: 1. app端主动设置是否进入低功耗模式
     * 2. 如果为低功耗模式，则不连接经典蓝牙
     */
    enterLowPowerMode = false;
    /**
     * 是否使用多设备管理
     */
    isUseMultiDevice = false;

    /**
     * 是否使用设备认证
     */
    isUseDeviceAuth = true;

    /*=======================================================================*
     * BLE相关配置
     *=======================================================================*/
    /**
     * BLE过滤规则的过滤标识
     */
    scanFilterData?: string;  // 搜索过滤数据
    /**
     * OTA过滤规则的过滤标识
     */
    otaScanFilterData: string = "JLOTA";  // OTA搜索过滤数据
    /**
     * BLE扫描策略
     * 说明: 决定设备过滤规则
     * 可选参数:{@link 0}  --- 不使用过滤规则
     * {@link 1} --- 使用全部过滤规则
     * {@link 2} --- 仅使用标识过滤规则
     * {@link 3} --- 仅使用hash加密过滤规则
     */
    bleScanStrategy: 0 | 1 | 2 | 3 = 1; //BLE过滤规则
    /**
     * 是否使用BLE配对方式
     */
    isUseBleBondWay = false;
    /**
     * 是否自动连接BLE
     */
    isAutoConnectBle = false;
    /**
     * 是否需要改变BLE的MTU
     */
    isNeedChangeBleMtu = false;
}

export class ParseDataUtil {
    /**
     * 解析广播包
     */
    public static parseBluetoothDeviceFound(option: BluetoothOption, device: BluetoothDevice) {
        let scanRecord: Uint8Array
        if (device.advertisData) {
            scanRecord = new Uint8Array(device.advertisData)
        } else {
            scanRecord = new Uint8Array(0)
        }
        const isBleEnableConnect = device.connectable
        const name = device.name
        // const deviceId = device.deviceId
        const RSSI = device.RSSI
        // logd("option :" + JSON.stringify(option))
        let bleScanMessage: BleScanMessage | undefined
        if (option.bleScanStrategy == 0) {//不使用过滤规则
            if (name !== "") {//不在发现数组内
                if (scanRecord != null && scanRecord.length > 2) {
                    // let offset = 0;
                    // while ((offset + 2) <= scanRecord.length) {
                    //     let totalLen = scanRecord[offset]
                    //     if (totalLen > 1 && totalLen < scanRecord.length) {  //自定义数据包最大长度31
                    //         let type = scanRecord[offset + 1]
                    //         if (type == 0xFF) { //匹配厂商标识
                    // let totalLen = scanRecord[offset]
                    // let otaScanRecord = new Uint8Array(totalLen - 1);
                    // otaScanRecord.set(scanRecord.slice(offset + 2, offset + 2 + otaScanRecord.length))
                    bleScanMessage = ParseDataUtil.parseWithOTAFlagFilter(scanRecord, option.otaScanFilterData);
                    //         }
                    //         offset += (totalLen + 1);
                    //     } else {
                    //         break;
                    //     }
                    // }
                }
                if (bleScanMessage==undefined) {
                    bleScanMessage = new BleScanMessage();
                }
                //                    JL_Log.d(TAG, "filterDevice : notify device : " + BluetoothUtil.printBtDeviceInfo(device) + "/n" + bleScanMessage);
                // this._discoveredDevices.push(device);
                if (bleScanMessage) {
                    bleScanMessage.rawData = scanRecord
                    bleScanMessage.rssi = RSSI
                    bleScanMessage.isEnableConnect = isBleEnableConnect
                    // this._RCSPBluetoothDiscoveryCbManager.onDiscoveryDevice(device, bleScanMessage)
                }
            }
        } else {
            bleScanMessage = ParseDataUtil.isFilterBleDevice(option, scanRecord);
            if (bleScanMessage != null && name !== "") {
                if (bleScanMessage.isEnableConnect && !isBleEnableConnect) {
                    bleScanMessage.isEnableConnect = false
                }
                bleScanMessage.rawData = scanRecord
                bleScanMessage.rssi = RSSI

                // if (bleScanMessage.showDialog) {
                //     this._RCSPBluetoothDiscoveryCbManager.onShowProductDialog(device, bleScanMessage);
                // }
                // if (!inArray(this._discoveredDevices, 'deviceId', deviceId)) {
                //     //                        JL_Log.d(TAG, "notify device 1111 : " + BluetoothUtil.printBtDeviceInfo(device) + "\n " + bleScanMessage);
                //     this._discoveredDevices.push(device);
                //     this._RCSPBluetoothDiscoveryCbManager.onDiscoveryDevice(device, bleScanMessage);
                // }
            }
        }
        return bleScanMessage
    }


    /**
     * 过滤蓝牙设备
     *
     * @param option   蓝牙配置
     * @param scanData 扫描数据
     * @return 过滤设备信息 若为null,则过滤
     */
    public static isFilterBleDevice(option: BluetoothOption, scanData: Uint8Array): BleScanMessage | undefined {
        let ret: BleScanMessage | undefined;
        if (option != null && scanData != null && scanData.length > 0) {
            // let len = scanData.length;
            // let offset = 0;
            // while ((offset + 2) <= len) {
            //     let totalLen = scanData[offset];
            //     if (totalLen > 1 && totalLen < len) {  //自定义数据包最大长度31
            //         let type = scanData[offset + 1]
            //         if (type == 0xFF) { //匹配厂商标识
            //             if (offset + 1 + totalLen > len) {
            //                 loge("-isFilterBleDevice- totalLen is error.");
            //                 break;
            //             }
            // let totalLen = scanData[offset];
            // let customDataLen = totalLen - 1; //去掉Type位,得到value长度
            // let data = new Uint8Array(customDataLen);
            // data.set(scanData.slice(offset + 2, offset + 2 + customDataLen)) //填充value数据
            // System.arraycopy(scanData, offset + 2, data, 0, customDataLen); //填充value数据
            const data = scanData
            if (scanData.length > 2) { //判断是否符合格式 （Vid(2B) + Custom_Data）
                let vidData = new Uint8Array(2);
                vidData.set(data.slice(0, vidData.length))
                // System.arraycopy(data, 0, vidData, 0, vidData.length); //获取vid
                let vid = ((0xff & vidData[1]) << 8) + (0xff & vidData[0])
                let messageLen = scanData.length - 2; //剩余自定义数据
                // if (3 + messageLen > totalLen) {
                //     loge("-isFilterBleDevice- messageLen is over limit.");
                //     // break;
                //     return ret
                // }
                let messageBuf = new Uint8Array(messageLen);
                messageBuf.set(data.slice(2, 2 + messageLen))
                // System.arraycopy(data, 2, messageBuf, 0, messageLen); //获取剩余自定义数据
                let bleFilterWay = 2;
                let mBleScanMessage: BleScanMessage | undefined = this.parseWithOTAFlagFilter(data, option.otaScanFilterData);//先验证广播包是不是OTA升级新回连广播包
                if (mBleScanMessage) {
                    ret = mBleScanMessage;
                    // break;
                    return ret
                }
                switch (option.bleScanStrategy) {
                    case 2:
                        mBleScanMessage = this.parseBleScanMsg(bleFilterWay, messageBuf, data);
                        break;
                    case 3:
                        bleFilterWay = 3;
                        mBleScanMessage = this.parseBleScanMsg(bleFilterWay, messageBuf, data);
                        break;
                    case 1:
                        bleFilterWay = 3;
                        mBleScanMessage = this.parseBleScanMsg(bleFilterWay, messageBuf, data);
                        if (mBleScanMessage == null) {
                            bleFilterWay = 2;
                            mBleScanMessage = this.parseBleScanMsg(bleFilterWay, messageBuf, data);
                        }
                        break;
                }
                if (mBleScanMessage != null) {
                    switch (bleFilterWay) {
                        case 2: {
                            mBleScanMessage.vid = vid;
                            let flagContent = mBleScanMessage.flagContent;
                            if (flagContent === option.scanFilterData) {
                                ret = mBleScanMessage;
                            }
                            break;
                        }
                        case 3: {
                            ret = mBleScanMessage;
                            break;
                        }
                    }
                }
                //             }
                //             break;
                //         }
                //         offset += (totalLen + 1);
                //     } else {
                //         break;
                //     }
                // }
            }
        }
        return ret;
    }

    /**
     * OTA标识过滤规则（兼容新回连方式）
     *
     * @param data 广播数据
     * @return BLE广播信息
     */
    public static parseWithOTAFlagFilter(data: Uint8Array, filterFlag: string | undefined): BleScanMessage | undefined {
        // logd("parseWithOTAFlagFilter data:" + ab2hex(data) + " filterFlag : " + filterFlag);
        let message: BleScanMessage | undefined;
        if (data != null && data.length > 7) {
            let offset = 2;
            let dataLen = data.length;
            let tempBuf = new Uint8Array(5);
            tempBuf.set(data.slice(offset, offset + tempBuf.length))
            let otaFlagBuf = new Uint8Array(5);
            for (let i = 0; i < otaFlagBuf.length; i++) {
                otaFlagBuf[i] = tempBuf[otaFlagBuf.length - i - 1];
            }
            // String otaFlag = new String(otaFlagBuf);
            let otaFlag = new String(otaFlagBuf)
            // logd("parseWithOTAFlagFilter otaFlag:" + otaFlag + " filterFlag : " + filterFlag);
            if (otaFlag.toLowerCase() !== filterFlag?.toLowerCase()) return undefined;
            offset += otaFlagBuf.length;
            let version = data[offset];
            offset += 1;
            // logd("parseWithOTAFlagFilter: version :" + version); 
            let bleAddressTempBuf = new Uint8Array(6);
            bleAddressTempBuf.set(data.slice(offset, offset + bleAddressTempBuf.length))
            // System.arraycopy(data, offset, bleAddressTempBuf, 0, bleAddressTempBuf.length);
            let bleAddressBuf = new Uint8Array(6);
            for (let i = 0; i < bleAddressTempBuf.length; i++) {
                bleAddressBuf[i] = bleAddressTempBuf[bleAddressTempBuf.length - i - 1];
            }
            offset += bleAddressBuf.length;
            // String bleAddress = hexDataCovetToAddress(bleAddressBuf);
            let bleAddress = hexDataCovetToAddress(bleAddressBuf)
            message = new BleScanMessage();
            message.isOTA = true
            message.otaADVVersion = version
            message.otaBleAddress = bleAddress
            switch (version) {
                default://todo 向后兼容
                case 0:
                    let reserveBuf = new Uint8Array(dataLen - offset);
                    // System.arraycopy(data, offset, reserveBuf, 0, reserveBuf.length);
                    reserveBuf.set(data.slice(offset, offset + reserveBuf.length))
                    message.otaADVReserve = reserveBuf
                    break;
                case 1:
                    let vid = (data[1] << 8) | data[0]; //获取vid
                    let uid = (data[offset + 1] << 8) | data[offset]; //获取uid
                    offset += 2;
                    let pid = (data[offset + 1] << 8) | data[offset]; //获取pid
                    offset += 2;
                    let value = data[offset];
                    let deviceType = value >> 4 & 0xff;
                    let deviceVersion = value & 0x0f;
                    let reserveBuf1 = new Uint8Array(dataLen - offset);
                    // System.arraycopy(data, offset, reserveBuf1, 0, reserveBuf1.length);
                    reserveBuf1.set(data.slice(offset, offset + reserveBuf1.length))
                    message.vid = vid
                    message.uid = uid
                    message.pid = pid
                    message.deviceType = deviceType
                    message.version = deviceVersion
                    message.otaADVReserve = reserveBuf1
                    break;
            }
        }
        return message;
    }
    /**
     * 解析BLE广播包
     *
     * @param filterWay        过滤规则
     * @param data             BLE广播包数据
     * @param manufacturerData 自定义厂商数据
     * @return BLE广播信息
     */
    private static parseBleScanMsg(filterWay: number, data: Uint8Array, manufacturerData: Uint8Array): BleScanMessage | undefined {
        let mBleScanMessage: BleScanMessage | undefined;
        switch (filterWay) {
            case 2: {
                mBleScanMessage = this.parseWithFlagFilter(data);
                break;
            }
            case 3: {
                mBleScanMessage = this.parseWithHashFilter(manufacturerData);
                break;
            }
        }
        return mBleScanMessage;
    }
    /**
     * 标识方式过滤规则
     *
     * @param data 广播数据
     * @return BLE广播信息
     */
    private static parseWithFlagFilter(data: Uint8Array): BleScanMessage | undefined {
        let message: BleScanMessage | undefined;
        if (data != null && data.length > 0) {
            let offset = 0;
            let dataLen = data.length;
            while (offset + 2 <= dataLen) {
                let len = data[offset];
                if (len > 0) {
                    let type = data[offset + 1];
                    let dataBuf = new Uint8Array(len - 1);
                    if (dataBuf.length > 0 && (dataBuf.length + offset + 2) <= dataLen) {
                        dataBuf.set(data.slice(offset + 2, offset + 2 + dataBuf.length))
                        // System.arraycopy(data, offset + 2, dataBuf, 0, dataBuf.length);
                        offset += (2 + dataBuf.length);
                        //                        JL_Log.d(TAG, "-parseBleScanMsg- type ：" + type + " ,data : " + CHexConver.byte2HexStr(dataBuf, dataBuf.length));
                    } else {
                        return undefined;
                    }
                    if (!message) {
                        message = new BleScanMessage();
                    }
                    switch (type) {
                        case 0://标识内容
                            let name = new String(dataBuf);
                            message.flagContent = name.toString();
                            continue;
                        case 1://标识设备是否已配对
                            if (dataBuf.length == 1) {
                                // message.setPairedFlag(CHexConverter.byteToInt(dataBuf[0]));
                                message.pairedFlag = dataBuf[0]
                            }
                            continue;
                        case 2://标识手机虚拟地址
                            message.phoneVirtualAddress = dataBuf
                            // message.setPhoneVirtualAddress(dataBuf);
                            continue;
                        case 3://标识VID和PID
                            if (dataBuf.length >= 3) {
                                message.pid = (dataBuf[1] << 8) | dataBuf[0]
                                message.showDialog = dataBuf[2] == 1
                                // message.setPid(CHexConverter.bytesToInt(dataBuf[1], dataBuf[0]));
                                // message.setShowDialog(CHexConverter.byteToInt(dataBuf[2]) == 1);
                            }
                            continue;
                        case 4://标识BR/EDR的信息
                            if (dataBuf.length >= 6) { //edr地址
                                let edrData = new Uint8Array(6);
                                edrData.set(dataBuf.slice(0, edrData.length))
                                // System.arraycopy(dataBuf, 0, edrData, 0, edrData.length);
                                let edr = hexDataCovetToAddress(edrData);
                                message.edrAddr = (edr);

                                if (dataBuf.length >= 7) { //edr连接状态
                                    // message.setEdrStatus(CHexConverter.byteToInt(dataBuf[6]));
                                    message.edrStatus = dataBuf[6]
                                    if (dataBuf.length >= 8) { //edr电量
                                        let value = dataBuf[7]
                                        // byte value = dataBuf[7];
                                        let isCharge = value >> 7 & 0x1;
                                        let quantity = value & 0x7f;
                                        //                                        JL_Log.d(TAG, "parseBleScanMsg::SCAN_TYPE_FLAG_EDR_MESSAGE >> value : " + CHexConverter.byte2HexStr(new byte[]{value}, 1)
                                        //                                                + ",isCharge : " + isCharge + ",quantity : " + quantity);
                                        message.isLeftCharging = (isCharge == 1);
                                        message.leftDeviceQuantity = (quantity);
                                    }
                                }
                            }
                            continue;
                        default:
                            break;
                    }
                } else {
                    break;
                }
            }
        }
        return message;
    }
    /**
     * Hash方式过滤规则
     *
     * @param data BLE广播数据
     * @return BLE广播信息
     */
    private static parseWithHashFilter(data: Uint8Array): BleScanMessage | undefined {

        //todo 有hash加密的东西，老项目有
        let message: BleScanMessage | undefined;
        //        JL_Log.d(TAG, "parseWithHashFilter :: data len = " + (data == null ? 0 : data.length));
        if (data != null && data.length == 29) {
            //           JL_Log.i(TAG, "parseWithHashFilter :: data = " + CHexConverter.byte2HexStr(data, data.length));
            let vid = (data[1] << 8) | data[0]; //获取vid
            let uid = (data[3] << 8) | data[2];//获取uid
            let pid = (data[5] << 8) | data[4]; //获取pid
            let deviceType = data[6] >> 4 & 0xFF;
            let version = data[6] & 0x0F; //获取协议版本
            //            JL_Log.i(TAG, "parseWithHashFilter :: vid = " + vid + ",uid = " + uid + ",pid = " + pid + ",deviceType = " + deviceType + ", version = " + version);
            message = new BleScanMessage()
            message.vid = vid
            message.uid = uid
            message.pid = pid
            message.deviceType = deviceType
            message.version = version
            message.showDialog = true
            message = this.parseTwsADV(data, message);
        }
        //      JL_Log.w(TAG, "parseWithHashFilter :: message = " + message);
        return message;
    }
    /**
     * 解析TWS广播数据
     *
     * @param data    BLE原始数据
     * @param message BLE广播数据
     * @return BLE广播数据
     */
    private static parseTwsADV(data: Uint8Array, message: BleScanMessage) {
        let result: BleScanMessage | undefined = undefined
        switch (message.deviceType) {
            case 0://音箱
            case 4:
            case 2:
                result = this.parseWithTwsVer1(data, message);
                break;
            case 1:
                result = this.parseWithChargingBin(data, message);
                break;
            case 3:
                result = this.parseWithTwsVer2(data, message);
                break;
            case 5:
                result = this.parseWithWatch(data, message);
                break;
        }
        return result;
    }
    //hash值校验
    private static hashCheck(data: Uint8Array) {
        if (data.length != 29) {
            return false;
        }
        let pt = data.slice(0, 16);
        let key = data.slice(13, 17)
        let hashOut = decryption(pt, key);
        let start = data.length - 8;
        let hash = data.slice(start)
        //return true;
        return ab2hex(new Uint8Array(hashOut)) === ab2hex(hash)
    }
    /**
     * 解析BR22的TWS广播数据
     *
     * @param data    BLE原始数据
     * @param message BLE广播数据
     * @return BLE广播数据
     */
    private static parseWithTwsVer1(data: Uint8Array, message: BleScanMessage): BleScanMessage | undefined {
        if (!this.hashCheck(data)) {
            return undefined
        }
        let offset = 7;
        let edrAddrBuf = new Uint8Array(6);
        edrAddrBuf.set(data.slice(offset, 13))
        offset += 6
        /*//地址反转
        byte[] edrAddrBufReverse = new byte[6];
        for (int i = 0; i < edrAddrBuf.length; i++){
            int j = edrAddrBuf.length - i - 1;
            edrAddrBufReverse[i] = edrAddrBuf[j];
        }*/
        let edrAddr = hexDataCovetToAddress(edrAddrBuf); //获取edr地址
        //            JL_Log.i(TAG, "parseWithTwsBR22 :: edrAddr = " + edrAddr);
        let action = data[offset]; //获取设备动作
        offset++
        let leftQuantityValue = data[offset];
        let isLeftCharge = leftQuantityValue >> 7 & 0x1;   //获取左耳（设备1）的充电标识
        let leftQuantity = leftQuantityValue & 0x7f;  //获取左耳（设备1）的电量
        offset++
        let rightQuantityValue = data[offset];
        let isRightCharge = rightQuantityValue >> 7 & 0x1;   //获取右耳（设备2）的充电标识
        let rightQuantity = rightQuantityValue & 0x7f;  //获取右耳（设备2）的电量
        offset++
        let chargingBinQuantityValue = data[offset];
        let isBinCharge = chargingBinQuantityValue >> 7 & 0x1;   //获取充电仓（设备3）的充电标识
        let chargingBinQuantity = chargingBinQuantityValue & 0x7f;  //获取充电仓（设备3）的电量
        offset++
        let seq = data[offset]  //获取序列号
        offset++
        let flag = 0;
        if (message.version == 1 || message.version == 2) {
            let tempValue = data[offset]
            offset++
            flag = tempValue & 0x01; //获取BLE连接标志位
        }
        let unusedSize = (message.version == 1 || message.version == 2) ? 2 : 3;
        offset += unusedSize //保留位

        //            JL_Log.w(TAG, "parseWithTwsBR22 :: hashBuf = " + CHexConverter.byte2HexStr(hashBuf, hashBuf.length) +
        //                    "\n decryptHash = " + CHexConverter.byte2HexStr(decryptHash, decryptHash.length));
        // if (Arrays.equals(hashBuf, decryptHash)) { //校验通过，结果赋值
        message.edrAddr = edrAddr;
        message.seq = seq;
        message.action = action
        message.edrStatus = (action);
        message.isLeftCharging = isLeftCharge == 1;
        message.leftDeviceQuantity = leftQuantity
        message.isRightCharging = isRightCharge == 1
        message.rightDeviceQuantity = rightQuantity
        message.isDeviceCharging = isBinCharge == 1
        message.chargingBinQuantity = chargingBinQuantity
        // message.setHash(hashBuf);
        if (message.version == 1) {
            message.isEnableConnect = flag == 0
        } else if (message.version == 2) {
            message.connectWay = flag
        }
        // } else {
        //     message = null;
        // }
        return message;
    }

    /**
     * 解析充电仓的广播数据
     *
     * @param data    BLE原始数据
     * @param message BLE广播数据
     * @return BLE广播数据
     */
    private static parseWithChargingBin(data: Uint8Array, message: BleScanMessage): BleScanMessage | undefined {
        if (!this.hashCheck(data)) {
            return undefined
        }
        let offset = 7
        let edrAddrBuf = new Uint8Array(6);
        edrAddrBuf.set(data.slice(offset, offset + 6))
        offset += 6
        /*//地址反转
        byte[] edrAddrBufReverse = new byte[6];
        for (int i = 0; i < edrAddrBuf.length; i++){
            int j = edrAddrBuf.length - i - 1;
            edrAddrBufReverse[i] = edrAddrBuf[j];
        }*/
        let edrAddr = hexDataCovetToAddress(edrAddrBuf); //获取edr地址
        //            JL_Log.i(TAG, "parseWithTwsBR22 :: edrAddr = " + edrAddr);
        let actionValue = data[offset];
        offset++
        let twsFlag = actionValue >> 7 & 0x1; //获取TWS标识
        let chargingBinStatus = actionValue >> 6 & 0x1; //获取充电仓状态
        let action = actionValue & 0x0f; //获取设备动作
        let leftQuantityValue = data[offset];
        offset++
        let isLeftCharge = leftQuantityValue >> 7 & 0x1;   //获取左耳（设备1）的充电标识
        let leftQuantity = leftQuantityValue & 0x7f;  //获取左耳（设备1）的电量
        let rightQuantityValue = data[offset];
        offset++
        let isRightCharge = rightQuantityValue >> 7 & 0x1;   //获取右耳（设备2）的充电标识
        let rightQuantity = rightQuantityValue & 0x7f;  //获取右耳（设备2）的电量
        let chargingBinQuantityValue = data[offset];
        offset++
        let isBinCharge = chargingBinQuantityValue >> 7 & 0x1;   //获取充电仓（设备3）的充电标识
        let chargingBinQuantity = chargingBinQuantityValue & 0x7f;  //获取充电仓（设备3）的电量
        let seq = data[offset];  //获取序列号
        offset++
        let mode = data[offset];
        offset++
        let chargingBinMode = mode >> 7 & 0x1; //获取充电仓模式
        let unused = new Uint8Array(2);
        unused.set(data.slice(offset, offset + 2)) //保留位
        // byte[] hashBuf = new byte[8];
        // byteBuffer = byteBuffer.get(hashBuf);
        // byteBuffer.rewind();
        // byte[] plaintext = new byte[16];
        // System.arraycopy(data, 0, plaintext, 0, plaintext.length);
        // //            JL_Log.i(TAG, "parseWithTwsBR22:: plaintext = " + CHexConverter.byte2HexStr(plaintext, plaintext.length));
        // byte[] key = new byte[4];
        // key[0] = actionValue;
        // key[1] = leftQuantityValue;
        // key[2] = rightQuantityValue;
        // key[3] = chargingBinQuantityValue;
        // //            JL_Log.i(TAG, "parseWithTwsBR22:: key = " + CHexConverter.byte2HexStr(key, key.length));
        // byte[] outputHashBuf = new byte[16];
        // HashDecryption.decrypt(plaintext, plaintext.length, key, key.length, outputHashBuf);
        // //            JL_Log.i(TAG, "parseWithTwsBR22:: outputHashBuf = " + CHexConverter.byte2HexStr(outputHashBuf, outputHashBuf.length));
        // byte[] decryptHash = new byte[8];
        // for (int i = 0; i < decryptHash.length; i++) { //赋值奇数位
        //     int j = i + (i + 1);
        //     decryptHash[i] = outputHashBuf[j];
        // }
        // //            JL_Log.w(TAG, "parseWithTwsBR22 :: hashBuf = " + CHexConverter.byte2HexStr(hashBuf, hashBuf.length) +
        // //                    "\n decryptHash = " + CHexConverter.byte2HexStr(decryptHash, decryptHash.length));
        // if (Arrays.equals(hashBuf, decryptHash)) { //校验通过，结果赋值

        message.edrAddr = edrAddr
        message.seq = seq
        message.twsFlag = twsFlag
        message.chargingBinStatus = chargingBinStatus
        message.action = action
        message.edrStatus = action;
        message.isLeftCharging = (isLeftCharge == 1);
        message.leftDeviceQuantity = (leftQuantity);
        message.isRightCharging = (isRightCharge == 1);
        message.rightDeviceQuantity = (rightQuantity);
        message.isDeviceCharging = (isBinCharge == 1);
        message.chargingBinQuantity = (chargingBinQuantity);
        message.chargingBinMode = (chargingBinMode);
        // } else {
        //     message = null;
        // }
        return message;
    }

    /**
     * 解析BR23的TWS广播数据
     *
     * @param data    BLE原始数据
     * @param message BLE广播数据
     */
    private static parseWithTwsVer2(data: Uint8Array, message: BleScanMessage): BleScanMessage {
        let offset = 7 //跳过前面数据
        let leftQuantityValue = data[offset];
        let isLeftCharge = leftQuantityValue >> 7 & 0x1;   //获取左耳（设备1）的充电标识
        let leftQuantity = leftQuantityValue & 0x7f;  //获取左耳（设备1）的电量
        offset++
        let rightQuantityValue = data[offset];
        let isRightCharge = rightQuantityValue >> 7 & 0x1;   //获取右耳（设备2）的充电标识
        let rightQuantity = rightQuantityValue & 0x7f;  //获取右耳（设备2）的电量
        offset++
        let actionValue = data[offset];
        let twsFlag = actionValue >> 7 & 0x1; //TWS标识
        let mainDevFlag = actionValue >> 6 & 0x1; //主从标识
        let isConnection = actionValue >> 5 & 0x1; //连接标识
        let action = actionValue & 0x0f; //设备动作/状态

        message.twsFlag = twsFlag
        message.mainDevFlag = mainDevFlag
        message.isEnableConnect = isConnection == 1
        message.action = action
        message.isLeftCharging = isLeftCharge == 1
        message.leftDeviceQuantity = leftQuantity
        message.isRightCharging = isRightCharge == 1
        message.rightDeviceQuantity = rightQuantity
        return message
    }

    /**
     * 解析手表类型设备的广播包
     *
     * @param data    裸数据
     * @param message 广播信息
     */
    private static parseWithWatch(data: Uint8Array, message: BleScanMessage) {
        let edrAddrBuf = new Uint8Array(6);
        edrAddrBuf.set(data.slice(7, 13))
        let edrAddr = hexDataCovetToAddress(edrAddrBuf); //获取edr地址
        message.edrAddr = edrAddr
        let status = data[13];
        message.connectWay = ((status >> 7) & 0x01);
        message.action = (status & 0x0f);
        return message
    }

}

export class StringUtil {
    /**
     * 
     */
    public static decodeGBK(data:number[] ) {
        return decodeURIComponent(escape(String.fromCharCode(...data)));
    }

    public static decodeUTF16LE(binaryStr: string) {
        console.log("decodeUTF16LE"+binaryStr);
        var cp = [];
        for (var i = 0; i < binaryStr.length; i += 2) {
            cp.push(
                binaryStr.charCodeAt(i) |
                (binaryStr.charCodeAt(i + 1) << 8)
            );
        }
        return String.fromCharCode.apply(String, cp);
    }
}
