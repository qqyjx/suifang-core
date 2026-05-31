/* 杰理协议操作封装库 version 1.1.0 , Gitee: https://gitee.com/Jieli-Tech/WeChat-Mini-Program-OTA, Github: https://github.com/Jieli-Tech/WeChat-Mini-Program-OTA*/
declare enum Connection {
    CONNECTION_DISCONNECT = 0,
    CONNECTION_CONNECTING = 1,
    CONNECTION_CONNECTED = 2
}
/** 设备 */
declare class Device {
    deviceId: string;
    name?: string;
    constructor(deviceId: string, name?: string);
    equals(o: Device | null): boolean;
}
/** 数据透传代理 */
interface IOProxy {
    /**
         * 传递设备的状态
         *
         * @param device 操作设备
         * @param status 设备状态
         *               <p>说明:
         *               1. 设备状态由库内定义，参考{@link }。
         *               2. 用户必须传入对应的状态码</p>
         */
    transmitDeviceStatus: (device: Device, status: Connection) => void;
    /**
     * 传递从设备接收到的数据
     *
     * @param device 操作设备
     * @param data   原始数据
     */
    transmitDeviceData: (device: Device, data: Uint8Array) => void;
    /**
     * 向设备发送RCSP数据包
     *
     * @param device 操作设备
     * @param data   RCSP数据包
     * @return 结果
     * <p>
     * 说明:
     * 1. 该方法需要用户自行实现
     * 2. 该方法运行在子线程，允许阻塞处理
     * 3. 该方法会回调完整的一包RCSP数据, 用户实现需要根据实际发送MTU进行分包处理
     * </p>
     */
    sendDataToDevice: (device: Device, data: Uint8Array) => boolean;
}
/** RCSP数据解析监听 */
interface OnRcspDataListener {
    /**回调接收到的RCSP命令包
   *
   * @param device  操作设备
   * @param command RCSP命令包
   */
    onRcspCommand: (device: Device, command: CommandBase) => void;
    /**回调接收到的RCSP回复包
     *
     * @param device  操作设备
     * @param command RCSP回复包
     */
    onRcspResponse: (device: Device, command: CommandBase) => void;
    /**回调错误事件
     *
     * @param device  操作设备
     * @param code    错误码 (参考{@link com.jieli.rcsp.data.constant.ErrorCode})
     * @param message 错误描述
     */
    onError: (device: Device | null, code: number, message: string) => void;
}
/** 命令解析器-基础解析器 */
declare abstract class BaseCmdParser {
    abstract createCommand(): CommandBase | null;
    convertToCmd(packet: RcspPacket): CommandBase | null;
}
/** Command-RCSP基础数据类型 */
declare class RcspPacket {
    static RCSP_HEAD: number[];
    static RCSP_END: number;
    private _isCommand;
    private _isNeedResponse;
    private _reserve;
    private _opCode;
    private _payload?;
    private _sn;
    isCommand(): boolean;
    setCommand(command: boolean): this;
    isNeedResponse(): boolean;
    setNeedResponse(needResponse: boolean): RcspPacket;
    getReserve(): number;
    setReserve(reserve: number): RcspPacket;
    getOpCode(): number;
    setOpCode(opCode: number): RcspPacket;
    getSn(): number;
    getPayload(): Uint8Array | undefined;
    setPayload(payload: Uint8Array): void;
    toData(): Uint8Array | null;
    parseData(data: Uint8Array): number;
}
/** Command-基础命令 */
declare class Command<P extends ParamBase, R extends ResponseBase> extends RcspPacket {
    private param;
    private response;
    constructor(opCode: number, param: P, response: R | null);
    getParam(): P;
    getResponse(): R | null;
    getSn(): number;
    setParam(param: P): void;
    setResponse(response: R | null): void;
    setSn(sn: number): void;
    setStatus(status: number): void;
    getStatus(): number;
    toData(): Uint8Array | null;
}
/** Command-参数基类 */
declare class ParamBase {
    private sn;
    private basePayload?;
    setSn(sn: number): void;
    getSn(): number;
    getData(): Uint8Array | undefined;
    setData(data: Uint8Array): void;
    toData(): Uint8Array;
    parseData(data: Uint8Array): number;
}
/** Command-回复数据基类 */
declare class ResponseBase {
    static STATUS_UNKNOWN: number;
    static STATUS_SUCCESS: number;
    static STATUS_FAILED: number;
    static STATUS_UNKNOWN_CMD: number;
    static STATUS_BUSY: number;
    static STATUS_NONE_RESOURCE: number;
    static STATUS_CRC_ERROR: number;
    static STATUS_ALL_DATA_CRC_ERROR: number;
    static STATUS_INVALID_PARAM: number;
    static STATUS_RESPONSE_DATA_OVER_LIMIT: number;
    private status;
    private sn;
    private payload?;
    getStatus(): number;
    getSn(): number;
    getPayload(): Uint8Array | undefined;
    setStatus(status: number): void;
    setSn(sn: number): void;
    setPayload(payload: Uint8Array): void;
    toData(): Uint8Array;
    parseData(data: Uint8Array): number;
}
declare class CommandBase extends Command<ParamBase, ResponseBase> {
}
/** Command-命令回调 */
interface CommandCallback<T extends CommandBase> {
    /**
   * 回调回复命令
   * @param device  操作对象
   * @param command 回复命令
   *                <p>说明:
   *                1. 若有回复的命令, 返回的是带回复数据的命令对象
   *                2. 若无回复的命令, 返回的是命令原型</p>
   */
    onCmdResponse(device: Device, command: T): void;
    /**
     * 回调错误事件
     * @param device  操作对象
     * @param code    错误码 (参考{@link com.jieli.rcsp.data.constant.ErrorCode})
     * @param message 错误描述
     */
    onError(device: Device, code: number, message: string): void;
}
/** 数据处理-基本数据信息 */
declare class BaseDataInfo {
    static DATA_TYPE_SEND: number;
    static DATA_TYPE_RECEIVE: number;
    device: Device;
    type: number;
    constructor(device: Device, type: number);
}
/** 数据处理-发送数据信息 */
declare class SendDataInfo extends BaseDataInfo {
    command: CommandBase;
    timeoutMs: number;
    callback: CommandCallback<CommandBase> | null;
    reSendCount: number;
    sendTimestamp: number;
    constructor(device: Device, command: CommandBase, timeoutMs: number, callback: CommandCallback<CommandBase> | null);
}
/** 数据处理-接收数据信息 */
declare class ReceiveDataInfo extends BaseDataInfo {
    data: Uint8Array;
    constructor(device: Device, data: Uint8Array);
}
/** 数据处理-RCSP数据处理器 */
declare class RCSPDataHandler {
    private SEND_AGAIN_LIMIT;
    protected ioProxy: IOProxy;
    listener: OnRcspDataListener;
    deviceMtuManager: DeviceMtuManager;
    private dataInfoCache;
    private sendInfoArray;
    private receiveInfoArray;
    private rcspParser;
    private isCanHandler;
    private sendTimeOutIDMap;
    constructor(ioProxy: IOProxy, deviceMtuManager: DeviceMtuManager, cmdParserMap: Map<number, BaseCmdParser>, listener: OnRcspDataListener);
    startHandler(): void;
    stopHandler(): void;
    addSendData(dataInfo: SendDataInfo): void;
    addReceiveData(dataInfo: ReceiveDataInfo): void;
    destroy(): void;
    private _checkDataAvailable;
    private _isCanHandle;
    private _writeDataToDevice;
    private _sendData;
    private _parseDataFromDevice;
    private _callbackError;
    private _callbackCmd;
    private _getRCSPSendMtu;
    private _getRCSPReceiveMtu;
    private _getSendDataKey;
    private _pollSendDataInfo;
}
/** 设备MTU管理器 */
declare abstract class DeviceMtuManager {
    abstract getReceiveMtu(device: Device): number | null;
    abstract getSendMtu(device: Device): number | null;
}

declare class LtvBean {
    type?: number;
    value?: Uint8Array;
    getLen(): number;
    toData(): Uint8Array;
    parseData(data: Uint8Array): number;
}
declare class ParamTargetInfo extends ParamBase {
    mask: number;
    platform: number;
    constructor(mask: number, platform: number);
    toData(): Uint8Array;
}
declare class ResponseTargetInfo extends ResponseBase {
    /**设备版本名称*/
    versionName?: string;
    /**设备版本信息*/
    versionCode: number;
    /**协议版本*/
    protocolVersion?: string;
    /**协议发送MTU*/
    sendMtu: number;
    /**协议接收MTU*/
    receiveMtu: number;
    /**经典蓝牙相关信息-经典蓝牙地址*/
    edrAddr?: string;
    /**经典蓝牙相关信息-经典蓝牙的连接状态*/
    edrStatus: number;
    /**经典蓝牙相关信息-经典蓝牙支持的协议*/
    edrProfile: number;
    /**BLE相关信息-BLE地址*/
    bleAddr?: string;
    /**ai平台相关参数-AI平台类型（0：图灵；1：deepbrain；2：小米）*/
    platform: number;
    /**ai平台相关参数-设备唯一标示*/
    license?: string;
    /**设备音量-当前值*/
    volume: number;
    /**设备音量-最大值*/
    maxVol: number;
    /**设备电量*/
    quantity: number;
    /**功能支持掩码*/
    functionMask: number;
    /**功能支持掩码-当前模式*/
    curFunction: number;
    /**功能支持掩码-支持蓝牙模式*/
    btEnable: boolean;
    /**功能支持掩码-支持设备音乐模式*/
    devMusicEnable: boolean;
    /**功能支持掩码-支持RTC模式(闹钟)*/
    rtcEnable: boolean;
    /**功能支持掩码-支持LineIn模式*/
    lineInEanble: boolean;
    /**功能支持掩码-支持FM接收模式*/
    fmEnable: boolean;
    /**功能支持掩码-支持灯光模式*/
    lightEnable: boolean;
    /**功能支持掩码-支持FM发射模式*/
    fmTXEnable: boolean;
    /**功能支持掩码-支持EQ模式*/
    eqEnable: boolean;
    /**是否支持usb、sd、linein不在线时显示功能图标*/
    supportOfflineShow: boolean;
    /**是否支持usb*/
    supportUsb: boolean;
    /**是否支持sd0*/
    supportSd0: boolean;
    /**是否支持sd1*/
    supportSd1: boolean;
    /**是否隐藏网络电台*/
    hideNetRadio: boolean;
    /**是否为标准sdk 默认false即默认为ai sdk*/
    sdkType: number;
    /**名字*/
    name?: string;
    /**产品ID*/
    pid: number;
    /**厂商ID*/
    vid: number;
    /**客户ID*/
    uid: number;
    /**强制升级*/
    mandatoryUpgradeFlag: number;
    /**请求升级标志*/
    requestOtaFlag: number;
    /**uboot 版本号*/
    ubootVersionCode: number;
    /**uboot 版本名称*/
    ubootVersionName?: string;
    /**是否支持双备份升级（单备份[false]，需要断开回连过程；双备份[true]，不需要断开回连过程）*/
    isSupportDoubleBackup: boolean;
    /**是否需要下载boot loader*/
    isNeedBootLoader: boolean;
    /**单备份OTA连接方式(00 -- 不使能 01 -- BLE  02 -- SPP)*/
    singleBackupOtaWay: number;
    /**拓展模式 0：不启用 1: 升级资源模式*/
    expandMode: number;
    /**是否允许连接 0 -- 允许 1 -- 不允许*/
    allowConnectFlag: number;
    /**用于服务器校验产品信息-认证秘钥*/
    authKey?: string;
    /**用于服务器校验产品信息-项目标识码*/
    projectCode?: string;
    /**用于自定义版本信息*/
    customVersionMsg?: string;
    /**是否仅仅连接ble设备，*/
    bleOnly: boolean;
    /**是否支持外设模式*/
    emitterSupport: boolean;
    /**外设状态：0x00:普通模式  0x01:外设模式*/
    emitterStatus: number;
    /**是否支持MD5读取*/
    isSupportMD5: boolean;
    /**是否游戏模式*/
    isGameMode: boolean;
    /**是否支持查找设备*/
    isSupportSearchDevice: boolean;
    /**是否支持音量同步*/
    supportVolumeSync: boolean;
    /**是否支持声卡功能*/
    supportSoundCard: boolean;
    /**是否支持外挂Flash传输功能*/
    supportExternalFlashTransfer: boolean;
    /**是否支持主动降噪功能*/
    supportAnc: boolean;
    /**禁止app调节设备eq*/
    banEq: boolean;
    /**大文件传输是否支持分包crc16校验*/
    supportPackageCrc16: boolean;
    /**以新的文件名方式读取固件文件(支持多设备)*/
    getFileByNameWithDev: boolean;
    /**联系人传输是否使用小文件方式*/
    contactsTransferBySmallFile: boolean;
    /**手表设置功能支持状态的掩码*/
    watchSettingMask: number;
    parseData(data: Uint8Array): number;
    private fillTargetInfo;
}

/**
   * 按键设置
   */
declare class KeySettings {
    keyNum: number;
    action: number;
    function: number;
    toData(): Uint8Array;
}
/**
   * 灯光设置
   */
declare class LedSettings {
    scene: number;
    effect: number;
    toData(): Uint8Array;
}
declare class ResponseGetADVInfo extends ResponseBase {
    /**
         * 厂商ID
         */
    pid: number;
    /**
     * 产品ID
     */
    vid: number;
    /**
     * 客户ID
     */
    uid: number;
    /**
     * 左设备（设备1）电量
     */
    leftDeviceQuantity: number;
    /**
     * 左设备（设备1）充电标识
     */
    isLeftCharging: boolean;
    /**
     * 右设备（设备2）电量
     */
    rightDeviceQuantity: number;
    /**
     * 右设备（设备2）充电标识
     */
    isRightCharging: boolean;
    /**
     * 充电仓（设备3）电量
     */
    chargingBinQuantity: number;
    /**
     * 充电仓（设备3）充电标识
     */
    isDeviceCharging: boolean;
    /**
     * 设备名
     */
    deviceName?: string;
    /**
     * 麦通道
     */
    micChannel: number;
    /**
     * 工作模式
     */
    workModel: number;
    /**
     * 按键设置
     */
    keySettingsList?: Array<KeySettings>;
    /**
     * 灯光设置
     */
    ledSettingsList?: Array<LedSettings>;
    /**
     * 入耳检测设置
     */
    inEarSettings: number;
    /**
     * 语言
     */
    language?: string;
    /**
     * ANC模式列表
     */
    modes?: Uint8Array;
    parseData(data: Uint8Array): number;
}
/**广播包信息 */
declare class ParamADVInfo extends ParamBase {
    private vid;
    private pid;
    private uid;
    private deviceType;
    private version;
    private showDialog;
    private edrAddr;
    private seq;
    private action;
    private leftDeviceQuantity;
    private leftCharging;
    private rightDeviceQuantity;
    private rightCharging;
    private chargingBinQuantity;
    private deviceCharging;
    getVid(): number;
    setVid(vid: number): ParamADVInfo;
    getPid(): number;
    setPid(pid: number): ParamADVInfo;
    getUid(): number;
    setUid(uid: number): ParamADVInfo;
    getDeviceType(): number;
    setDeviceType(deviceType: number): ParamADVInfo;
    getVersion(): number;
    setVersion(version: number): ParamADVInfo;
    isShowDialog(): boolean;
    setShowDialog(showDialog: boolean): ParamADVInfo;
    getEdrAddr(): string;
    setEdrAddr(edrAddr: string): ParamADVInfo;
    getSeq(): number;
    setSeq(seq: number): ParamADVInfo;
    getAction(): number;
    setAction(action: number): ParamADVInfo;
    getLeftDeviceQuantity(): number;
    setLeftDeviceQuantity(leftDeviceQuantity: number): ParamADVInfo;
    isLeftCharging(): boolean;
    setLeftCharging(leftCharging: boolean): ParamADVInfo;
    getRightDeviceQuantity(): number;
    setRightDeviceQuantity(rightDeviceQuantity: number): ParamADVInfo;
    isRightCharging(): boolean;
    setRightCharging(rightCharging: boolean): ParamADVInfo;
    getChargingBinQuantity(): number;
    setChargingBinQuantity(chargingBinQuantity: number): ParamADVInfo;
    isDeviceCharging(): boolean;
    setDeviceCharging(deviceCharging: boolean): ParamADVInfo;
    parseData(data: Uint8Array): number;
    private fillADVInfo;
}

declare class ResponseReadExternalFlashInfo extends ResponseBase {
    /**
     * flash大小
     */
    flash: number;
    /**
     * fat系统实际大小
     */
    fatSize: number;
    /**
     * 系统类型,0:FATFS,1:RCSP
     */
    system: number;
    /**
     * 系统当前状态（系统处于异常时，恢复系统），0x00:系统正常，0x01:系统处于写错误
     */
    systemStatus: number;
    /**
    * 版本号
    */
    version: number;
    /**
     * 发包窗口大小
     */
    block: number;
    /**
     * 簇大小
     * (以256Bytes为单位的倍数)例子: cluster = 16，实际大小为 256*16=4096
     */
    cluster: number;
    /**
     * 匹配版本数据长度
     */
    matchVersionLen: number;
    /**
     * 匹配版本列表
     */
    matchVersions: string[] | undefined;
    /**
     * 可发送数据最大值
     */
    revMTU: number;
    /**
     * 屏幕宽度
     */
    width: number;
    /**
     * 屏幕高度
     */
    height: number;
    parseData(data: Uint8Array): number;
}

/** 设备信息 */
declare class DeviceInfo extends ResponseTargetInfo {
}
/** 设备信息管理器 */
declare class DeviceInfoManager extends DeviceMtuManager {
    private deviceInfoMap;
    release(): void;
    getReceiveMtu(device: Device): number | null;
    getSendMtu(device: Device): number | null;
    getDeviceInfo(device: Device): DeviceInfo | undefined;
    removeDeviceInfo(device: Device): boolean | undefined;
    updateDeviceInfo(device: Device, deviceInfo: DeviceInfo): void;
}
/** 数据发送回调 */
interface OnSendDataCallback {
    /**
     * 向设备发送RCSP数据包
     *
     * @param device 操作设备
     * @param data   RCSP数据包
     * @return 结果
     * <p>
     * 说明:
     * 1. 该方法需要用户自行实现
     * 2. 该方法运行在子线程，允许阻塞处理
     * 3. 该方法会回调完整的一包RCSP数据, 用户实现需要根据实际发送MTU进行分包处理
     * </p>
     */
    sendDataToDevice: (device: Device, data: Uint8Array) => boolean;
}
/** Rcsp事件回调*/
declare class OnRcspCallback {
    /**
     * Rcsp协议初始化回调
     *
     * @param device 已连接设备
     * @param isInit 初始化结果
     */
    onRcspInit(device: Device | null, isInit: boolean): void;
    /**
     * 设备主动发送的rcsp命令回调
     *
     * @param device  已连接设备
     * @param command RCSP命令
     */
    onRcspCommand(device: Device | null, command: CommandBase): void;
    /**
 * 设备回复的rcsp命令回调
 *
 * @param device  已连接设备
 * @param command RCSP命令
 */
    onRcspResponse(device: Device | null, command: CommandBase): void;
    /**
     * 设备主动发送的数据命令回调
     *
     * @param device  已连接设备
     * @param dataCmd 数据命令
     */
    onRcspDataCmd(device: Device | null, dataCmd: CommandBase): void;
    /**
     * RCSP错误事件回调
     *
     * @param device  设备对象
     * @param error   错误码 (参考{@link com.jieli.rcsp.data.constant.ErrorCode})
     * @param message 错误描述
     */
    onRcspError(device: Device | null, error: number, message: string): void;
    /**
     * 需要强制升级回调
     *
     * @param device 需要强制升级的设备
     */
    onMandatoryUpgrade(device: Device | null): void;
    /**
     * 设备连接状态
     *
     * @param device 蓝牙设备
     * @param status 连接状态
     */
    onConnectStateChange(device: Device | null, status: Connection): void;
}
interface OnResultCallback<T> {
    /**回调结果
     *
     * @param device 操作设备
     * @param result 结果
     */
    onResult(device: Device, result: T): void;
    /**回调错误结果
     *
     * @param device  操作设备
     * @param code    错误码 (参考{@link com.jieli.rcsp.data.constant.ErrorCode})
     * @param message 错误信
     */
    onError(device: Device, code: number, message: string): void;
}
/** Rcsp协议实现 */
declare class RcspOpImpl implements IOProxy {
    protected mRCSPDataHandler: RCSPDataHandler;
    private mDeviceInfoManager;
    private mRcspCallbackManager;
    private mCmdSnGenerator;
    private mTargetDevice;
    private mOnSendDataCallback?;
    constructor();
    getUsingDevice(): Device | null;
    setOnSendDataCallback(callback: OnSendDataCallback | undefined): void;
    isDeviceConnected(): boolean;
    getDeviceInfo(device: Device): DeviceInfo | undefined;
    addOnRcspCallback(callback: OnRcspCallback): void;
    removeOnRcspCallback(callback: OnRcspCallback): void;
    transmitDeviceStatus(device: Device, status: Connection): void;
    transmitDeviceData(device: Device, data: Uint8Array): void;
    sendDataToDevice(device: Device, data: Uint8Array): boolean;
    sendRCSPCommand(device: Device, command: CommandBase, timeoutMs: number, callback: CommandCallback<CommandBase> | null): void;
    syncDeviceInfo(device: Device, param: ParamTargetInfo, callback: OnResultCallback<ResponseTargetInfo>): void;
    destroy(): void;
    getDeviceInfoManager(): DeviceInfoManager;
    private callbackCmdError;
    private callbackError;
    private sendCommand;
    private handleDeviceStatus;
    private handleDeviceConnectedEvent;
    private checkIsValidDevice;
    private handleDeviceReceiveData;
    private dealWithRcspCommand;
    private dealWithRcspResponse;
}

interface MutexTaskOp {
    /**
     * 结束任务
    */
    tryToFinish(): void;
}
declare class MutexTask {
    name: string;
    time: number;
    private _lastRunTime;
    private _isFinish;
    private _op;
    constructor(name: string, time: number, op: MutexTaskOp);
    updateTime(): void;
    tryToFinish(): void;
    onTaskFinish(): void;
    isFinished(): boolean;
    isTimeOut(): boolean;
}
declare class MutexTaskManager {
    private _task;
    isFree(): boolean;
    excuteTask(task: MutexTask): boolean;
    finishCurrentTask(): void;
}

/**
 * 操作类的事件-基类
*/
declare class OperateEventBase {
}
declare class OperateEventCallbackBase<T extends OperateEventBase> {
    onEvent(_event: T): void;
}
declare class OperateEventCallbackManager<T extends OperateEventCallbackBase<OperateEventBase>> extends OperateEventCallbackBase<OperateEventBase> {
    private mCallbacks;
    registerEventCallback(callback: T): void;
    unregisterEventCallback(callback: T): void;
    release(): void;
    getCallbacks(): Array<T>;
}
declare abstract class OperateBase<T extends OperateEventCallbackBase<OperateEventBase>> extends Object {
    protected COMMAND_TIME_OUT: number;
    protected _rcspOpImpl: RcspOpImpl;
    protected _eventCallbackManager?: OperateEventCallbackManager<T>;
    protected _mutexTaskManager?: MutexTaskManager;
    constructor(rcspOpImpl: RcspOpImpl, mutexTaskManager?: MutexTaskManager | undefined);
    release(): void;
    removeAllEventCallback(): void;
    /*********************************公共基础方法***********************************/
    registerEventCallback(callback: T): void;
    unregisterEventCallback(callback: T): void;
    doCallbackAction(obj: {
        action: (c: T) => void;
    }): void;
    isUsingDevice(device: Device | null): boolean;
    getDeviceInfo(): DeviceInfo | undefined;
    protected _sendRcspCommand(cmd: CommandBase, callback: CommandCallback<CommandBase> | null): void;
    protected _buildCommandErrorString(code: number, msg: string): string;
    protected _buildCommandResponseStatusString(code: number, msg?: string): string;
    /**
    * 设置固件状态信息
    */
    protected _sendSetSysInfoCommand(functionCode: number, dataList: LtvBean[] | undefined, callback?: {
        onSuccess: () => void;
        onFailed: (errorCode: number, errorMsg: string) => void;
    }): void;
    /**
     * 获取固件状态信息
     */
    protected _sendGetSysInfoCommand(functionCode: number, mask: number, callback?: {
        onSuccess: (ltvBeans: LtvBean[] | undefined) => void;
        onFailed: (errorCode: number, errorMsg: string) => void;
    }): void;
}
declare class OperaterFunction<T extends OperateEventCallbackBase<OperateEventBase>> extends OperateBase<T> {
    protected _setFunctionAsync(functionCode: number, childCmd: number, extendData: Uint8Array | undefined): Promise<boolean>;
}
declare function CRC16(temp: Uint8Array, len: number, offset: number, lastCrc: number): number;
declare function clone(obj: Object): any;

declare const opBase_CRC16: typeof CRC16;
type opBase_OperateBase<T extends OperateEventCallbackBase<OperateEventBase>> = OperateBase<T>;
declare const opBase_OperateBase: typeof OperateBase;
type opBase_OperateEventBase = OperateEventBase;
declare const opBase_OperateEventBase: typeof OperateEventBase;
type opBase_OperateEventCallbackBase<T extends OperateEventBase> = OperateEventCallbackBase<T>;
declare const opBase_OperateEventCallbackBase: typeof OperateEventCallbackBase;
type opBase_OperateEventCallbackManager<T extends OperateEventCallbackBase<OperateEventBase>> = OperateEventCallbackManager<T>;
declare const opBase_OperateEventCallbackManager: typeof OperateEventCallbackManager;
type opBase_OperaterFunction<T extends OperateEventCallbackBase<OperateEventBase>> = OperaterFunction<T>;
declare const opBase_OperaterFunction: typeof OperaterFunction;
declare const opBase_clone: typeof clone;
declare namespace opBase {
  export {
    opBase_CRC16 as CRC16,
    opBase_OperateBase as OperateBase,
    opBase_OperateEventBase as OperateEventBase,
    opBase_OperateEventCallbackBase as OperateEventCallbackBase,
    opBase_OperateEventCallbackManager as OperateEventCallbackManager,
    opBase_OperaterFunction as OperaterFunction,
    opBase_clone as clone,
  };
}

interface IOperateFile {
    getSdCardBeans(): SDCardBean[] | undefined;
    getOnLineSdCardBeans(): SDCardBean[] | undefined;
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackFile): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackFile): void;
    notifySdCardBeanDirectoryUpdate(sdCardBean: SDCardBean): void;
}
declare class OperaterEventFile extends OperateEventBase {
    /**
     * NONE: 无事件
     * onSdCardChange :SD卡状态发生变化
     * */
    type: "NONE" | "onSdCardChange" | "onSdCardDirectoryChange";
    /** sd卡状态发生变化*/
    onSdCardChange?: {
        sdCardBeans: Array<SDCardBean>;
    };
    /** sd卡目录发生变化*/
    onSdCardDirectoryChange?: {
        sdCardBean: SDCardBean;
    };
}
declare class OperaterEventCallbackFile extends OperateEventCallbackBase<OperaterEventFile> {
}
declare class OperateFile extends OperateBase<OperaterEventCallbackFile> implements IOperateFile {
    private _rcspCallback;
    private _sdCardBeans;
    constructor(rcspOpImpl: RcspOpImpl);
    release(): void;
    getSdCardBeans(): SDCardBean[] | undefined;
    getOnLineSdCardBeans(): SDCardBean[] | undefined;
    notifySdCardBeanDirectoryUpdate(sdCardBean: SDCardBean): void;
    private _handleSdCardChange;
}
declare class SDCardBean {
    static SD: number;
    static USB: number;
    static FLASH: number;
    static LINEIN: number;
    static FLASH_2: number;
    static INDEX_USB: number;
    static INDEX_SD0: number;
    static INDEX_SD1: number;
    static INDEX_FLASH: number;
    static INDEX_LINE_IN: number;
    static INDEX_FLASH2: number;
    index: number;
    type: number;
    name: string | undefined;
    devHandler: number;
    online: boolean;
}
declare class FileBaseUtil {
    static parseSDCardBeans(data: Uint8Array, deviceInfo: DeviceInfo): SDCardBean[];
    /**
     * 获取设备名称
     *
     * @param devIndex 设备句柄
     * @return 字符串
     */
    static getDevName(devIndex: number): string;
}

type opFile_FileBaseUtil = FileBaseUtil;
declare const opFile_FileBaseUtil: typeof FileBaseUtil;
type opFile_IOperateFile = IOperateFile;
type opFile_OperateFile = OperateFile;
declare const opFile_OperateFile: typeof OperateFile;
type opFile_OperaterEventCallbackFile = OperaterEventCallbackFile;
declare const opFile_OperaterEventCallbackFile: typeof OperaterEventCallbackFile;
type opFile_OperaterEventFile = OperaterEventFile;
declare const opFile_OperaterEventFile: typeof OperaterEventFile;
type opFile_SDCardBean = SDCardBean;
declare const opFile_SDCardBean: typeof SDCardBean;
declare namespace opFile {
  export {
    opFile_FileBaseUtil as FileBaseUtil,
    opFile_IOperateFile as IOperateFile,
    opFile_OperateFile as OperateFile,
    opFile_OperaterEventCallbackFile as OperaterEventCallbackFile,
    opFile_OperaterEventFile as OperaterEventFile,
    opFile_SDCardBean as SDCardBean,
  };
}

declare enum ReadFileResultCode {
    RESULT_OK = 0,
    RESULT_DEV_BUSY = 1,
    RESULT_DEV_DATA_SEND_FAIL = 2,
    RESULT_START_BROWSE_FAIL = 3,
    RESULT_BROWSE_DATA_ERROR = 4,
    RESULT_TASK_RESPONSE_TIMEOUT = 5
}
/**
 * 文件结构
*/
declare class FileStruct {
    file: boolean;
    unicode: boolean;
    cluster: number;
    fileNum: number;
    name: string | undefined;
    devIndex: number | undefined;
}
declare class OperaterEventDirectoryBrowse extends OperateEventBase {
    /**
     * NONE: 无事件
     * onDirectoryChange ：目录发生变化
     *
     * */
    type: "NONE" | "onDirectoryChange";
    /** 目录发生变化*/
    onDirectoryChange?: {
        folder: File;
    };
}
declare class OperaterEventCallbackDirectoryBrowse extends OperateEventCallbackBase<OperaterEventDirectoryBrowse> {
}
declare class File {
    private _fs;
    /** 路径格式：dev/root/xxx/xxx   */
    path: string;
    constructor(fs: FileSystem, path: string);
    getName(): string | undefined;
    list(): string[] | undefined;
    listFile(): File[] | undefined;
    getParent(): string | undefined;
    getParentFile(): File | undefined;
    getCluster(): number | undefined;
    getPathCluster(): number[] | undefined;
    getDevIndex(): number | undefined;
    /**当前文件夹下的子文件是否都已加载*/
    isLoadFinished(): boolean;
    isDirectory(): boolean;
    isFormatUnicode(): boolean;
    /**删除该文件的本地缓存*/
    delete(): boolean;
    /**删除该文件的全部子文件本地缓存*/
    deleteAllChild(): boolean;
    updateLoadFinished(isLoadFinished: boolean): void;
    exist(): boolean;
}
declare class INode {
    parentInodeIndex: number;
    name: string;
    inodeIndex: number;
    childInodeIndexs: number[];
    accessAttr: number;
    boolenAttr: number;
    cluster: number;
    storage: number;
    constructor(name: string);
}
declare class FileSystem {
    static ACCESS_OK: number;
    static ACCESS_LOAD_FINESHED: number;
    static ACCESS_FORMAT_UNICODE: number;
    static BA_REGULAR: number;
    static BA_DIRECTORY: number;
    rootNum: number;
    autoRootInodeIndex: number;
    autoInodeIndex: number;
    _inodeMap: Map<number, INode>;
    separator: string;
    _cacheLastINode: any;
    /**
     * 加载文件
     * @param parent 父文件 undefined:认为是根目录
     * @param fileStructs
     * @param isLoadFinished 父文件是否加载完成
     */
    loadFolder(parent: File | undefined, fileStruct: FileStruct): void;
    getOnLineStorageDevs(): File[];
    /** 列出文件,文件：undefined，文件夹列出下面的文件path*/
    list(file: File): string[] | undefined;
    checkAccess(file: File, access: number): boolean;
    getBooleanAttributes(file: File): number;
    updateCheckAccess(file: File, access: number, newStatus: boolean): void;
    getCluster(file: File): number | undefined;
    getPathCluster(file: File): number[] | undefined;
    getDevIndex(file: File): number | undefined;
    delete(file: File): boolean;
    deleteAllChild(file: File): boolean;
    getFileByPath(path: string): File | undefined;
    private _deleteINode;
    private _deleteINodeChilds;
    private _getINodePath;
    private _getINodeByPath;
    private _findChildINodeByName;
    private _getRootINodeByName;
    private _autoInodeIndex;
    private _autoRootInodeIndex;
    private _checkAccess;
}
interface IOperateDirectoryBrowse {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackDirectoryBrowse): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackDirectoryBrowse): void;
    /** 加载该文件夹下更多文件列表*/
    loadMore(file: File, readPageSize?: number): Promise<File>;
    /** 该文件进入下一级*/
    appendBrowse(file: File, readPageSize?: number, maxDepth?: number): Promise<File>;
    /** 该文件返回上一级*/
    backBrowse(file: File): Promise<File>;
    /** 获取在线存储设备列表*/
    getOnLineStorageDevs(): File[];
    /** 根据路径获取文件*/
    getFileByPath(path: string): File | undefined;
    /** 删除文件*/
    deleteFile(files: File[]): Promise<any>;
    /** 重读文件夹*/
    reloadFolder(folder: File): Promise<File>;
}
declare class OperateDirectoryBrowse extends OperateBase<OperaterEventCallbackDirectoryBrowse> implements IOperateDirectoryBrowse {
    private ROOT_NAME;
    private _isReading;
    private _ReadDirectoryTaskManager;
    private _FileSystem;
    private _rcspCallback;
    private _isStartFileBrowse;
    private _cacheBrowseBuffer;
    private _OperaterEventCallbackFile;
    private _opFile;
    private _MutexTask;
    readPageSize: number;
    private _MutexTaskOp;
    constructor(rcspOpImpl: RcspOpImpl, opFile: OperateFile, mutexTaskManager: MutexTaskManager);
    release(): void;
    loadMore(file: File, readPageSize?: number): Promise<File>;
    cleanCache(file: File): Promise<boolean>;
    /**
     * 进入下一级
     * @param file
     */
    appendBrowse(file: File, readPageSize?: number, maxDepth?: number): Promise<File>;
    /**
     * 返回上一级
     */
    backBrowse(file: File): Promise<File>;
    getFileByPath(path: string): File | undefined;
    getOnLineStorageDevs(): File[];
    deleteFile(files: File[]): Promise<unknown>;
    /** 重读文件夹*/
    reloadFolder(folder: File): Promise<File>;
    private _readDirectory;
    private _isReadings;
    private _handleSdCardChange;
    private _handleSdCardDirectoryChange;
    private _handleDirectoryChange;
}

type opDirectoryBrowse_File = File;
declare const opDirectoryBrowse_File: typeof File;
type opDirectoryBrowse_FileStruct = FileStruct;
declare const opDirectoryBrowse_FileStruct: typeof FileStruct;
type opDirectoryBrowse_IOperateDirectoryBrowse = IOperateDirectoryBrowse;
type opDirectoryBrowse_OperateDirectoryBrowse = OperateDirectoryBrowse;
declare const opDirectoryBrowse_OperateDirectoryBrowse: typeof OperateDirectoryBrowse;
type opDirectoryBrowse_OperaterEventCallbackDirectoryBrowse = OperaterEventCallbackDirectoryBrowse;
declare const opDirectoryBrowse_OperaterEventCallbackDirectoryBrowse: typeof OperaterEventCallbackDirectoryBrowse;
type opDirectoryBrowse_OperaterEventDirectoryBrowse = OperaterEventDirectoryBrowse;
declare const opDirectoryBrowse_OperaterEventDirectoryBrowse: typeof OperaterEventDirectoryBrowse;
type opDirectoryBrowse_ReadFileResultCode = ReadFileResultCode;
declare const opDirectoryBrowse_ReadFileResultCode: typeof ReadFileResultCode;
declare namespace opDirectoryBrowse {
  export {
    opDirectoryBrowse_File as File,
    opDirectoryBrowse_FileStruct as FileStruct,
    opDirectoryBrowse_IOperateDirectoryBrowse as IOperateDirectoryBrowse,
    opDirectoryBrowse_OperateDirectoryBrowse as OperateDirectoryBrowse,
    opDirectoryBrowse_OperaterEventCallbackDirectoryBrowse as OperaterEventCallbackDirectoryBrowse,
    opDirectoryBrowse_OperaterEventDirectoryBrowse as OperaterEventDirectoryBrowse,
    opDirectoryBrowse_ReadFileResultCode as ReadFileResultCode,
  };
}

declare class OperaterEventLargeFileTrans extends OperateEventBase {
    /**
     * NONE: 无事件
     * */
    type: "NONE";
}
declare class OperaterEventCallbackLargeFileTrans extends OperateEventCallbackBase<OperaterEventLargeFileTrans> {
}
interface IOperateLargeFileTrans {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackLargeFileTrans): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackLargeFileTrans): void;
    excuteTransferTask(param: {
        fileBuffer: ArrayBuffer;
        sdCardBean: SDCardBean;
        isSupportPackageCrc16: boolean;
        fileName: string;
        lastModifyTime: number;
        fileNameEncodeFormat: number;
        callback: TransferTaskCallback$1;
    }): boolean;
    cancelTransfer(): void;
    isFree(): boolean;
}
interface TransferTaskCallback$1 {
    /**
     *
     * @param code
     *               1//创建文件失败
     *               2//环境准备失败
     *               3//读取crc失败
     *               4//重命名失败
     *               5//传输过程中-数据越界
     *               6//传输超时
     *
     *               1001：大文件传输结束-写失败
     *               1002：大文件传输结束-数据超出范围
     *               1003：大文件传输结束-crc校验失败
     *               1004：大文件传输结束-内存不足
     *
     */
    onError(code: number): void;
    onStart(): void;
    onProgress(progress: number): void;
    onSuccess(): void;
    /**
     * @param code 0:app取消，1：dev取消
    */
    onCancel(code: number): void;
}
declare class OperateLargeFileTrans extends OperateBase<OperaterEventCallbackLargeFileTrans> implements IOperateLargeFileTrans {
    private _currentTask;
    private _opFile;
    private _rcspCallback;
    private _MutexTaskOp;
    private _TransferTaskOp;
    constructor(rcspOpImpl: RcspOpImpl, opFile: OperateFile, mutexTaskManager: MutexTaskManager);
    release(): void;
    excuteTransferTask(param: {
        fileBuffer: ArrayBuffer;
        sdCardBean: SDCardBean;
        isSupportPackageCrc16: boolean;
        fileName: string;
        lastModifyTime: number;
        fileNameEncodeFormat: number;
        callback: TransferTaskCallback$1;
    }): boolean;
    cancelTransfer(): void;
    isFree(): boolean;
}

type opLargeFileTrans_IOperateLargeFileTrans = IOperateLargeFileTrans;
type opLargeFileTrans_OperateLargeFileTrans = OperateLargeFileTrans;
declare const opLargeFileTrans_OperateLargeFileTrans: typeof OperateLargeFileTrans;
type opLargeFileTrans_OperaterEventCallbackLargeFileTrans = OperaterEventCallbackLargeFileTrans;
declare const opLargeFileTrans_OperaterEventCallbackLargeFileTrans: typeof OperaterEventCallbackLargeFileTrans;
type opLargeFileTrans_OperaterEventLargeFileTrans = OperaterEventLargeFileTrans;
declare const opLargeFileTrans_OperaterEventLargeFileTrans: typeof OperaterEventLargeFileTrans;
declare namespace opLargeFileTrans {
  export {
    opLargeFileTrans_IOperateLargeFileTrans as IOperateLargeFileTrans,
    opLargeFileTrans_OperateLargeFileTrans as OperateLargeFileTrans,
    opLargeFileTrans_OperaterEventCallbackLargeFileTrans as OperaterEventCallbackLargeFileTrans,
    opLargeFileTrans_OperaterEventLargeFileTrans as OperaterEventLargeFileTrans,
    TransferTaskCallback$1 as TransferTaskCallback,
  };
}

declare class SystemInfo {
    /** 电量*/
    battery?: number;
    /** 音量*/
    volume?: number;
    /** 设备在线设备信息*/
    SdCardBeanList?: Array<SDCardBean>;
    /** 错误状态，参考ResponseBase.STATUS_XXXX*/
    errorStatusReport?: number;
    /** 当前模式
     *  0x00:蓝牙模式, 0x01:音乐模式,0x02:RTC模式，0x03:LineIn模式,0x04:FM模式,0x05:Light模式,0x06:FMTX模式,0x07:EQ模式,0x16:低功耗模式,
    */
    currentMode?: number;
    /** 通话状态,0x00:空闲，0x01:通话中*/
    callStatus?: number;
}
declare class OperaterEventSystemInfo extends OperateEventBase {
    /**
     * NONE: 无事件
     * SystemInfoChange : 系统信息变化
     * */
    type: "NONE" | "SystemInfoChange";
    SystemInfoChange?: {
        systemInfo: SystemInfo;
    };
}
declare class OperaterEventCallbackSystemInfo extends OperateEventCallbackBase<OperaterEventSystemInfo> {
}
interface IOperateSystemInfo {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackSystemInfo): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackSystemInfo): void;
    /** 重新获取系统信息*/
    reloadSystemInfo(): Promise<SystemInfo>;
    /** 获取系统信息*/
    getSystemInfo(): Promise<SystemInfo>;
}
declare class OperateSystemInfo extends OperateBase<OperaterEventCallbackSystemInfo> implements IOperateSystemInfo {
    private _rcspCallback;
    private _systemInfo;
    constructor(rcspOpImpl: RcspOpImpl, mutexTaskManager: MutexTaskManager);
    release(): void;
    /** 重新获取系统信息*/
    reloadSystemInfo(): Promise<SystemInfo>;
    /** 获取系统信息*/
    getSystemInfo(): Promise<SystemInfo>;
    private _readSystemInfo;
    private _parseSystemInfo;
    private _isSystemInfoChange;
    private _IsSdCardBeanListChange;
    private _handleSystemInfo;
    private _handleSystemInfoChange;
}

type opSystemInfo_IOperateSystemInfo = IOperateSystemInfo;
type opSystemInfo_OperateSystemInfo = OperateSystemInfo;
declare const opSystemInfo_OperateSystemInfo: typeof OperateSystemInfo;
type opSystemInfo_OperaterEventCallbackSystemInfo = OperaterEventCallbackSystemInfo;
declare const opSystemInfo_OperaterEventCallbackSystemInfo: typeof OperaterEventCallbackSystemInfo;
type opSystemInfo_OperaterEventSystemInfo = OperaterEventSystemInfo;
declare const opSystemInfo_OperaterEventSystemInfo: typeof OperaterEventSystemInfo;
type opSystemInfo_SystemInfo = SystemInfo;
declare const opSystemInfo_SystemInfo: typeof SystemInfo;
declare namespace opSystemInfo {
  export {
    opSystemInfo_IOperateSystemInfo as IOperateSystemInfo,
    opSystemInfo_OperateSystemInfo as OperateSystemInfo,
    opSystemInfo_OperaterEventCallbackSystemInfo as OperaterEventCallbackSystemInfo,
    opSystemInfo_OperaterEventSystemInfo as OperaterEventSystemInfo,
    opSystemInfo_SystemInfo as SystemInfo,
  };
}

declare class OperaterEventWatch extends OperateEventBase {
    /**
     * NONE: 无事件
     * FlashInfoChange : flash信息变化
     * */
    type: "NONE" | "FlashInfoChange";
    FlashInfoChange?: {
        flashInfo: ResponseReadExternalFlashInfo;
    };
}
declare class OperaterEventCallbackWatch extends OperateEventCallbackBase<OperaterEventWatch> {
}
interface IOperateWatch {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackWatch): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackWatch): void;
    /** 重新获取flash信息*/
    reloadFlashInfo(): Promise<ResponseReadExternalFlashInfo>;
    /** 获取flash信息*/
    getFlashInfo(): Promise<ResponseReadExternalFlashInfo>;
}
declare class OperateWatch extends OperateBase<OperaterEventCallbackWatch> implements IOperateWatch {
    private _rcspCallback;
    private _flashInfo;
    constructor(rcspOpImpl: RcspOpImpl, mutexTaskManager: MutexTaskManager);
    release(): void;
    /** 重新获取flash信息*/
    reloadFlashInfo(): Promise<ResponseReadExternalFlashInfo>;
    /** 获取flash信息*/
    getFlashInfo(): Promise<ResponseReadExternalFlashInfo>;
    private _readFlashInfo;
    private _handleFlashInfoChange;
}

type opWatch_IOperateWatch = IOperateWatch;
type opWatch_OperateWatch = OperateWatch;
declare const opWatch_OperateWatch: typeof OperateWatch;
type opWatch_OperaterEventCallbackWatch = OperaterEventCallbackWatch;
declare const opWatch_OperaterEventCallbackWatch: typeof OperaterEventCallbackWatch;
type opWatch_OperaterEventWatch = OperaterEventWatch;
declare const opWatch_OperaterEventWatch: typeof OperaterEventWatch;
declare namespace opWatch {
  export {
    opWatch_IOperateWatch as IOperateWatch,
    opWatch_OperateWatch as OperateWatch,
    opWatch_OperaterEventCallbackWatch as OperaterEventCallbackWatch,
    opWatch_OperaterEventWatch as OperaterEventWatch,
  };
}

declare class OperaterEventWatchDial extends OperateEventBase {
    /**
     * NONE: 无事件
     * UseDialChange : 当前表盘变化
     * WatchResourseFileListChange:手表Flash资源文件列表变化
     * */
    type: "NONE" | "UseDialChange" | "WatchResourseFileListChange";
    UseDialChange?: {
        dial: File;
    };
    WatchResourseFileListChange?: {
        fileList: Array<File>;
    };
}
declare class OperaterEventCallbackWatchDial extends OperateEventCallbackBase<OperaterEventWatchDial> {
}
interface IOperateWatchDial {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackWatchDial): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackWatchDial): void;
    /** 设置过滤文件列表(默认是过滤了系统资源文件：JL,FONT,SIDEBAR)*/
    setIgnoreFileList(ignoreList: Array<string>): void;
    /** 获取手表Flash资源文件列表(除系统资源文件：JL,FONT,SIDEBAR)*/
    getWatchResourseFileList(): Promise<Array<File>>;
    /** 重新获取手表Flash资源文件列表(除系统资源文件：JL,FONT,SIDEBAR)*/
    reloadWatchResourseFileList(): Promise<Array<File>>;
    /** 获取正在使用的表盘*/
    getUsingDial(): Promise<File>;
    /** 取消添加手表Flash资源文件*/
    cancelAddWatchResourseFile(): void;
    /** 添加手表Flash资源文件*/
    addWatchResourseFile(fileData: ArrayBuffer, fileName: string, lastModifyTime: number, refreshDirectory: boolean, callback: TransferTaskCallback$1): Promise<File | string | undefined>;
    /** 设置当前表盘*/
    setUsingDial(dial: File): Promise<boolean>;
    /** 获取表盘版本信息*/
    getDialVersionInfo(dial: File): Promise<string>;
    /** 获取表盘背景*/
    getDialCustomBackground(dial: File): Promise<File | null>;
    /** 设置自定义表盘背景*/
    setDialCustomBackground(dialBackground: File | undefined): Promise<boolean>;
    /** 删除表盘*/
    deleteDial(dial: File): Promise<boolean>;
    /** 删除自定义表盘背景*/
    deleteDialCustomBackground(dialBackground: File): Promise<boolean>;
}
declare class OperateWatchDial extends OperateBase<OperaterEventCallbackWatchDial> implements IOperateWatchDial {
    private _ignoreFileList;
    private _opFile;
    private _operateDirectoryBrowse;
    private _operateLargeFileTrans;
    private _currentUsingDial;
    private _rcspCallback;
    private _OperaterEventCallbackDirectoryBrowse;
    private _isTransferingFile;
    constructor(rcspOpImpl: RcspOpImpl, opFile: IOperateFile, operateDirectoryBrowse: IOperateDirectoryBrowse, operateLargeFileTrans: IOperateLargeFileTrans, mutexTaskManager: MutexTaskManager);
    release(): void;
    /** 设置过滤文件列表(默认是过滤了系统资源文件：JL,FONT,SIDEBAR)*/
    setIgnoreFileList(ignoreList: Array<string>): void;
    /** 获取正在使用的表盘*/
    getUsingDial(): Promise<File>;
    /** 获取手表Flash资源文件列表(除系统资源文件：JL,FONT,SIDEBAR)*/
    getWatchResourseFileList(): Promise<File[]>;
    reloadWatchResourseFileList(): Promise<File[]>;
    /** 取消添加手表Flash资源文件*/
    cancelAddWatchResourseFile(): void;
    /** 添加表盘*/
    addWatchResourseFile(fileData: ArrayBuffer, fileName: string, lastModifyTime: number, refreshDirectory: boolean, callback: TransferTaskCallback$1): Promise<string | File | undefined>;
    /** 设置当前表盘*/
    setUsingDial(dial: File): Promise<boolean>;
    /** 获取表盘版本信息*/
    getDialVersionInfo(dial: File): Promise<string>;
    /** 获取表盘背景*/
    getDialCustomBackground(dial: File): Promise<File | null>;
    /** 设置自定义表盘背景*/
    setDialCustomBackground(dialBackground: File | undefined): Promise<boolean>;
    /** 删除表盘*/
    deleteDial(dial: File): Promise<boolean>;
    /** 删除表盘*/
    deleteDialCustomBackground(dialBackground: File): Promise<boolean>;
    private _getFlashDevs;
    private _getFlashSDCardBean;
    private _loaderMoreRootFolder;
    private _filtFileList;
    private _handleWatchResourseFileListChange;
    private _handleUseDialChange;
}

type opWatchDial_IOperateWatchDial = IOperateWatchDial;
type opWatchDial_OperateWatchDial = OperateWatchDial;
declare const opWatchDial_OperateWatchDial: typeof OperateWatchDial;
type opWatchDial_OperaterEventCallbackWatchDial = OperaterEventCallbackWatchDial;
declare const opWatchDial_OperaterEventCallbackWatchDial: typeof OperaterEventCallbackWatchDial;
type opWatchDial_OperaterEventWatchDial = OperaterEventWatchDial;
declare const opWatchDial_OperaterEventWatchDial: typeof OperaterEventWatchDial;
declare namespace opWatchDial {
  export {
    opWatchDial_IOperateWatchDial as IOperateWatchDial,
    opWatchDial_OperateWatchDial as OperateWatchDial,
    opWatchDial_OperaterEventCallbackWatchDial as OperaterEventCallbackWatchDial,
    opWatchDial_OperaterEventWatchDial as OperaterEventWatchDial,
  };
}

declare class OperaterEventLargeFileGet extends OperateEventBase {
    /**
     * NONE: 无事件
     * */
    type: "NONE";
}
declare class OperaterEventCallbackLargeFileGet extends OperateEventCallbackBase<OperaterEventLargeFileGet> {
}
interface IOperateLargeFileGet {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackLargeFileGet): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackLargeFileGet): void;
    /** 以文件名方式读取-已弃用*/
    excuteGetTaskByFileName(param: {
        offset: number;
        fileName: string;
        callback: TransferTaskCallback;
    }): boolean;
    /** 以簇号方式读取*/
    excuteGetTaskByFileCluster(param: {
        offset: number;
        sdCardBean: SDCardBean;
        cluster: number;
        callback: TransferTaskCallback;
    }): boolean;
    /** 以文件名方式读取指定存储设备*/
    excuteGetTaskByFileNameAndDevHandle(param: {
        offset: number;
        sdCardBean: SDCardBean;
        fileName: string;
        callback: TransferTaskCallback;
    }): boolean;
    /**取消获取*/
    cancelGet(): void;
    /** 是否空闲*/
    isFree(): boolean;
}
interface TransferTaskCallback {
    /**
     *
     * @param code
     * 1：开始读取文件失败
     * 2：总数据长度不对
     * 3：传输超时
     *
     * 100x:对应设备端协议 结束命令的错误码 x
     */
    onError(code: number): void;
    /**传输开始**/
    onStart(): void;
    /**传输进度
     * @param packectData 分包数据
     * **/
    onProgress(progress: number, packectData?: ArrayBuffer): void;
    /**传输成功
     * @param data 总包数据
     * **/
    onSuccess(data: ArrayBuffer): void;
    /**
     * @param code
     * 0:主动取消
     * 1:丢包
     * 2：crc错误
    */
    onCancel(code: number): void;
}
declare class OperateLargeFileGet extends OperateBase<OperaterEventCallbackLargeFileGet> implements IOperateLargeFileGet {
    private _currentTask;
    private _rcspCallback;
    private _MutexTaskOp;
    private _TransferTaskOp;
    constructor(rcspOpImpl: RcspOpImpl, mutexTaskManager: MutexTaskManager);
    release(): void;
    /** 以文件名方式读取*/
    excuteGetTaskByFileName(param: {
        offset: number;
        fileName: string;
        callback: TransferTaskCallback;
    }): boolean;
    /** 以簇号方式读取*/
    excuteGetTaskByFileCluster(param: {
        offset: number;
        sdCardBean: SDCardBean;
        cluster: number;
        callback: TransferTaskCallback;
    }): boolean;
    /** 以文件名方式读取指定设备*/
    excuteGetTaskByFileNameAndDevHandle(param: {
        offset: number;
        sdCardBean: SDCardBean;
        fileName: string;
        callback: TransferTaskCallback;
    }): boolean;
    private _excuteGetTask;
    cancelGet(): void;
    isFree(): boolean;
}

type opLargeFileGet_IOperateLargeFileGet = IOperateLargeFileGet;
type opLargeFileGet_OperateLargeFileGet = OperateLargeFileGet;
declare const opLargeFileGet_OperateLargeFileGet: typeof OperateLargeFileGet;
type opLargeFileGet_OperaterEventCallbackLargeFileGet = OperaterEventCallbackLargeFileGet;
declare const opLargeFileGet_OperaterEventCallbackLargeFileGet: typeof OperaterEventCallbackLargeFileGet;
type opLargeFileGet_OperaterEventLargeFileGet = OperaterEventLargeFileGet;
declare const opLargeFileGet_OperaterEventLargeFileGet: typeof OperaterEventLargeFileGet;
type opLargeFileGet_TransferTaskCallback = TransferTaskCallback;
declare namespace opLargeFileGet {
  export {
    opLargeFileGet_IOperateLargeFileGet as IOperateLargeFileGet,
    opLargeFileGet_OperateLargeFileGet as OperateLargeFileGet,
    opLargeFileGet_OperaterEventCallbackLargeFileGet as OperaterEventCallbackLargeFileGet,
    opLargeFileGet_OperaterEventLargeFileGet as OperaterEventLargeFileGet,
    opLargeFileGet_TransferTaskCallback as TransferTaskCallback,
  };
}

declare class BleScanMessage {
    /**
    * 原始数据
    */
    rawData?: Uint8Array;
    /**
     * 过滤标识
     */
    flagContent?: string;
    pairedFlag?: number;
    phoneVirtualAddress?: Uint8Array;
    /**
     * 厂商ID
     */
    pid: number;
    /**
     * 产品ID
     */
    vid: number;
    /**
     * 客户ID
     */
    uid: number;
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
    deviceType: number;
    /**
     * 协议版本
     */
    version: number;
    /**
     * 弹窗标志
     * <p>
     * 说明: 1 - true 0 - false
     * 描述: AC692X之前的广播包的属性
     * </p>
     */
    showDialog: boolean;
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
    edrStatus: number;
    /**
     * 左设备（设备1）电量
     * <p>
     * 说明：取值范围 0-100 (0 视为设备不在线)
     * </p>
     */
    leftDeviceQuantity: number;
    /**
     * 左设备（设备1）充电标识
     */
    isLeftCharging: boolean;
    /**
     * 右设备（设备2）电量
     * <p>
     * 说明：取值范围 0-100 (0 视为设备不在线)
     * </p>
     */
    rightDeviceQuantity: number;
    /**
     * 右设备（设备2）充电标识
     */
    isRightCharging: boolean;
    /**
     * 充电仓（设备3）电量
     * <p>
     * 说明：取值范围 0-100 (0 视为设备不在线)
     * </p>
     */
    chargingBinQuantity: number;
    /**
     * 充电仓（设备3）充电标识
     */
    isDeviceCharging: boolean;
    /**
     * TWS配对标识
     * <p>
     * 0 -- 未配对
     * 1 -- 已配对
     * </p>
     */
    twsFlag: number;
    /**
     * 充电仓状态
     * <p>
     * 0 --- 关盖
     * 1 --- 开盖
     * </p>
     */
    chargingBinStatus: number;
    /**
     * 主从标识
     * <p>
     * 0 --- 从机
     * 1 --- 主机
     * </p>
     */
    mainDevFlag: number;
    /**
     * 行为/状态
     * <p>
     * 说明: 表示耳机状态
     * 可选值：{@link 0} 不显示弹窗,
     * {@link 1} 蓝牙未连接,
     * {@link 2} 蓝牙已连接,
     * {@link 3} 蓝牙正在连接
     */
    action: number;
    /**
     * 序列号
     * <p>
     * 说明:标识当前广播的序号，用于判断广播是否被过滤或过时
     * </p>
     */
    seq: number;
    /**
     * 充电仓模式
     * <p>
     * 0 --- 充电模式
     * 1 --- 发射模式
     * </p>
     */
    chargingBinMode: number;
    /**
     * 哈希值
     */
    hash?: Uint8Array;
    /**
     * 接收信号强度
     */
    rssi?: number;
    /**
     * 是否允许连接
     * <p>说明: 默认允许连接，特殊情况不允许</p>
     */
    isEnableConnect: boolean;
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
    isOTA: boolean;
    /**
     * OTA广播包版本号
     */
    otaADVVersion: number;
    /**
     * OTA广播包-原BLE地址
     */
    otaBleAddress?: string;
    /**
     * OTA广播包-保留位数据
     */
    otaADVReserve?: Uint8Array;
}

declare class OperaterEventHeadSetInfo extends OperateEventBase {
    /**
     * NONE: 无事件
     * */
    type: "NONE" | "DeviceBroadcast" | "DeviceSettingInfo" | "TWSStatusChange" | "DeviceRequestOperate";
    /**设备广播信息变化*/
    DeviceBroadcast?: {
        broadcast: BleScanMessage;
    };
    /**设备设置信息变化*/
    DeviceSettingInfo?: {
        mask: number;
        settingInfo: ResponseGetADVInfo;
    };
    /**TWS状态变化*/
    TWSStatusChange?: {
        isTWSConnected: boolean;
    };
    /**设备请求操作通知*/
    DeviceRequestOperate?: {
        operate: TWSInfoMaskBit;
    };
}
declare class OperaterEventCallbackHeadset extends OperateEventCallbackBase<OperaterEventHeadSetInfo> {
}
interface IOperateHeadSetInfo {
    /** 移除全部事件监听回调*/
    removeAllEventCallback(): void;
    /** 注册回调*/
    registerEventCallback(callback: OperaterEventCallbackHeadset): void;
    /** 注销回调*/
    unregisterEventCallback(callback: OperaterEventCallbackHeadset): void;
    /** 获取设备信息*/
    getDeviceInfo(): DeviceInfo | undefined;
    /********************************TWS命令相关功能*****************************************/
    /** 是否支持tws命令*/
    isSupportTwsCmd(): Promise<boolean>;
    /** 获取ADV信息
     * @param isRealtime 是否实时获取。默认读取缓存
    */
    getADVInfo(isRealtime?: boolean): Promise<ResponseGetADVInfo>;
    /** 获取ADV信息
    * @param isRealtime 是否实时获取。默认读取缓存
    */
    getADVInfoSync(): ResponseGetADVInfo | undefined;
    /** 设置设备TWS广播包
     *	@param enable true:打开，false：关闭
    */
    setADVBroadcast(enable: boolean): Promise<boolean>;
    /**
     * 根据掩码获取设备TWS信息(0xC1)
     *	@param mask 参考TWSInfoMaskBit （最大4个byte）
     */
    readDevTWSSettingsInfo(mask: number): Promise<ResponseGetADVInfo>;
    /**
     * **非特殊情况下不使用**
     * 根据掩码设置设备TWS信息（LTV格式）
     */
    setDevTWSSettingsInfoByMask(ltvbean: LtvBean): Promise<boolean>;
    /**
     * 设置设备名称（有长度限制，不能超过20Bytes）
    */
    setDevName(devName: string): Promise<boolean> | undefined;
    /**
     * 配置按键功能设置
    */
    setKeySettings(keySettingsList: KeySettings[]): Promise<boolean>;
    /**
     * 配置灯光效果设置
    */
    setLedSettings(ledSettingsList: LedSettings[]): Promise<boolean>;
    /**
     * 配置工作模式
    */
    setWorkMode(workMode: number): Promise<boolean>;
    /**
     * 配置麦克风通道参数
    */
    setMicChannel(micChannel: number): Promise<boolean>;
    /**
     * 更新连接设备时间(精确度只到秒)
    */
    updateDevConnectedTime(date: Date): Promise<boolean>;
    /********************************降噪功能*****************************************/
    /**
     * 是否支持主动降噪功能
    */
    isSupportAnc(): Promise<boolean>;
    /**
     * 获取当前噪声模式
     * @param isRealtime 是否实时获取。默认读取缓存
    */
    getCurrentVoiceMode(isRealtime?: boolean): Promise<ANCVoiceMode>;
    /**
     * 设置当前噪声模式
    */
    setCurrentVoiceMode(mode: ANCVoiceMode): Promise<boolean>;
    /**
     * 获取噪声模式列表
     * @param isRealtime 是否实时获取。默认读取缓存
    */
    getVoiceModeList(isRealtime?: boolean): Promise<Array<ANCVoiceMode>>;
}
declare enum TWSInfoMaskBit {
    ADV_TYPE_BATTERY_QUANTITY = 0,
    ADV_TYPE_DEVICE_NAME = 1,
    ADV_TYPE_KEY_SETTINGS = 2,
    ADV_TYPE_LED_SETTINGS = 3,
    ADV_TYPE_MIC_CHANNEL_SETTINGS = 4,
    ADV_TYPE_WORK_MODE = 5,
    ADV_TYPE_PRODUCT_MESSAGE = 6,
    ADV_TYPE_CONNECTED_TIME = 7,
    ADV_TYPE_IN_EAR_CHECK = 8,
    ADV_TYPE_LANGUAGE = 9,
    ADV_TYPE_ANC_MODE_LIST = 10
}
declare enum TWSInfoSettingResult {
    SUCCESS = 0,
    GAME_MODE_SET_FAIL = 1,
    BT_NAME_MAX_LEN_ERROR = 2,
    NOT_BT_MODE_SET_LED_FAIL = 3
}
declare class OperateHeadSetInfo extends OperateBase<OperaterEventCallbackHeadset> implements IOperateHeadSetInfo {
    private _rcspCallback;
    private _responseGetADVInfo?;
    constructor(rcspOpImpl: RcspOpImpl);
    release(): void;
    getDeviceInfo(): DeviceInfo | undefined;
    /********************************TWS命令相关功能*****************************************/
    isSupportTwsCmd(): Promise<boolean>;
    /** 获取ADV信息*/
    getADVInfo(isRealtime?: boolean): Promise<ResponseGetADVInfo>;
    getADVInfoSync(): ResponseGetADVInfo | undefined;
    /** 设置设备TWS广播包
     *	@param enable true:打开，false：关闭
    */
    setADVBroadcast(enable: boolean): Promise<boolean>;
    /** 根据掩码获取设备TWS信息(0xC1)
     *	@param mask 参考TWSInfoMaskBit （最大4个byte）
     */
    readDevTWSSettingsInfo(mask: number): Promise<ResponseGetADVInfo>;
    /****非特殊情况下不使用**
     * 根据掩码设置设备TWS信息（LTV格式）
     */
    setDevTWSSettingsInfoByMask(ltvbean: LtvBean): Promise<boolean>;
    /** 设置设备名称（有长度限制，不能超过20Bytes）
    */
    setDevName(devName: string): Promise<boolean>;
    /** 配置按键功能设置
    */
    setKeySettings(keySettingsList: KeySettings[]): Promise<boolean>;
    /** 配置灯光效果设置
    */
    setLedSettings(ledSettingsList: LedSettings[]): Promise<boolean>;
    /** 配置工作模式
    */
    setWorkMode(workMode: number): Promise<boolean>;
    /** 配置麦克风通道参数
    */
    setMicChannel(micChannel: number): Promise<boolean>;
    /** 更新连接设备时间(精确度只到秒)
    */
    updateDevConnectedTime(date: Date): Promise<boolean>;
    /********************************降噪功能*****************************************/
    /** /是否支持主动降噪功能
       */
    isSupportAnc(): Promise<boolean>;
    private _cacheCurrentANCVoiceMode;
    private _cacheANCVoiceModeList;
    /** 获取当前噪声模式
    */
    getCurrentVoiceMode(isRealtime?: boolean): Promise<ANCVoiceMode>;
    /** 设置当前噪声模式
    */
    setCurrentVoiceMode(mode: ANCVoiceMode): Promise<boolean>;
    /** 获取噪声模式列表
    */
    getVoiceModeList(isRealtime?: boolean): Promise<Array<ANCVoiceMode>>;
    /********************************TWS命令相关功能*****************************************/
    private _setDevTWSSettingsInfoByMask;
    private _onDeviceBroadcast;
    private _onTwsStatusChange;
    private _onDeviceRequestOp;
    private _onDeviceSettingsInfo;
    /********************************降噪功能*****************************************/
    private _parseANCVoidModeData;
}
declare class HeadSetInfoUtil {
    /**
 * 从ADV信息转变成设备广播信息
 *
 * @param param ADV信息
 * @return 设备广播信息
 */
    static convertBroadcastFromAdvInfo(param: ParamADVInfo): BleScanMessage;
    /**
    * 从设备广播信息转变成ADV信息
    *
    * @param param 设备广播信息
    * @return ADV信息
    */
    static convertAdvInfoFromBleScanMessage(param: BleScanMessage): ResponseGetADVInfo;
    /**
     * 从ADV信息转变成设备信息
     *
     * @param param ADV信息
     * @return 设备信息
     */
    static convertADVInfoFromBroadcast(param: ParamADVInfo): ResponseGetADVInfo;
    /**
     * 比较ADV信息是否相等
     *
     * @param adv  ADV信息
     * @param adv1 ADV信息
     * @return 结果
     */
    static equalsADVInfo(adv: ResponseGetADVInfo, adv1: ResponseGetADVInfo): boolean;
    /**
     * 合并ADV信息
     *
     * @param oldInfo 旧的ADV信息
     * @param newInfo 新的ADV信息
     * @return 合并后的ADV信息
     */
    static mergeADVInfo(oldInfo: ResponseGetADVInfo, newInfo: ResponseGetADVInfo): ResponseGetADVInfo;
}
declare enum ANCVoiceModeType {
    /**默认*/
    TYPE_IDEL = -1,
    /**关闭*/
    TYPE_CLOSE = 0,
    TYPE_DENOISE = 1,
    TYPE_TRANSPARENT = 2
}
declare class ANCVoiceMode {
    type: ANCVoiceModeType;
    leftMax: number;
    leftVal: number;
    rightMax: number;
    rightVal: number;
}

type opHeadset_ANCVoiceMode = ANCVoiceMode;
declare const opHeadset_ANCVoiceMode: typeof ANCVoiceMode;
type opHeadset_ANCVoiceModeType = ANCVoiceModeType;
declare const opHeadset_ANCVoiceModeType: typeof ANCVoiceModeType;
type opHeadset_HeadSetInfoUtil = HeadSetInfoUtil;
declare const opHeadset_HeadSetInfoUtil: typeof HeadSetInfoUtil;
type opHeadset_IOperateHeadSetInfo = IOperateHeadSetInfo;
type opHeadset_OperateHeadSetInfo = OperateHeadSetInfo;
declare const opHeadset_OperateHeadSetInfo: typeof OperateHeadSetInfo;
type opHeadset_OperaterEventCallbackHeadset = OperaterEventCallbackHeadset;
declare const opHeadset_OperaterEventCallbackHeadset: typeof OperaterEventCallbackHeadset;
type opHeadset_OperaterEventHeadSetInfo = OperaterEventHeadSetInfo;
declare const opHeadset_OperaterEventHeadSetInfo: typeof OperaterEventHeadSetInfo;
type opHeadset_TWSInfoMaskBit = TWSInfoMaskBit;
declare const opHeadset_TWSInfoMaskBit: typeof TWSInfoMaskBit;
type opHeadset_TWSInfoSettingResult = TWSInfoSettingResult;
declare const opHeadset_TWSInfoSettingResult: typeof TWSInfoSettingResult;
declare namespace opHeadset {
  export {
    opHeadset_ANCVoiceMode as ANCVoiceMode,
    opHeadset_ANCVoiceModeType as ANCVoiceModeType,
    opHeadset_HeadSetInfoUtil as HeadSetInfoUtil,
    opHeadset_IOperateHeadSetInfo as IOperateHeadSetInfo,
    opHeadset_OperateHeadSetInfo as OperateHeadSetInfo,
    opHeadset_OperaterEventCallbackHeadset as OperaterEventCallbackHeadset,
    opHeadset_OperaterEventHeadSetInfo as OperaterEventHeadSetInfo,
    opHeadset_TWSInfoMaskBit as TWSInfoMaskBit,
    opHeadset_TWSInfoSettingResult as TWSInfoSettingResult,
  };
}

/**  字符串 转换 ArrayBuffer*/
declare function string2buffer(str: any): ArrayBufferLike;
declare function stringToUTF16E(str: any): number[];
declare function dataToString(charset: any, data: any): string;
declare function stringToData(charset: any, str: any): any[] | ArrayBuffer;
/**  字符串 转换 ArrayBuffer*/
declare function utf8StringToArrayBuffer(str: any): ArrayBufferLike;
declare function arrayBufferToUtf8String(buf: any): string;
declare function getStringHashCode(str: any): number;

declare const StringUtil_arrayBufferToUtf8String: typeof arrayBufferToUtf8String;
declare const StringUtil_dataToString: typeof dataToString;
declare const StringUtil_getStringHashCode: typeof getStringHashCode;
declare const StringUtil_string2buffer: typeof string2buffer;
declare const StringUtil_stringToData: typeof stringToData;
declare const StringUtil_stringToUTF16E: typeof stringToUTF16E;
declare const StringUtil_utf8StringToArrayBuffer: typeof utf8StringToArrayBuffer;
declare namespace StringUtil {
  export {
    StringUtil_arrayBufferToUtf8String as arrayBufferToUtf8String,
    StringUtil_dataToString as dataToString,
    StringUtil_getStringHashCode as getStringHashCode,
    StringUtil_string2buffer as string2buffer,
    StringUtil_stringToData as stringToData,
    StringUtil_stringToUTF16E as stringToUTF16E,
    StringUtil_utf8StringToArrayBuffer as utf8StringToArrayBuffer,
  };
}

declare class RcspOperateWrapper {
    private _rcspOpImpl;
    private _operaterArray;
    private _mutexLockManager;
    constructor(rcspOpImpl: RcspOpImpl);
    /**
     *
     */
    getRcspOpImpl(): RcspOpImpl;
    getOperaterByClass<T extends OperateBase<OperateEventCallbackBase<OperateEventBase>>>(classPrototype: T): T | undefined;
    /**
     * 释放
     */
    release(): void;
}

export { opBase as OPBase, opDirectoryBrowse as OPDirectoryBrowse, opFile as OPFile, opHeadset as OPHeadSet, opLargeFileGet as OPLargerFileGet, opLargeFileTrans as OPLargerFileTrans, opSystemInfo as OPSystemInfo, opWatch as OPWatch, opWatchDial as OPWatchDial, RcspOperateWrapper, StringUtil };
/* follow me on Github! */
