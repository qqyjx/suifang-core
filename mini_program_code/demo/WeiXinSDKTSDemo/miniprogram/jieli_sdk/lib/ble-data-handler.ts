import { ab2hex } from "../utils/log";
import { logv, logd, loge } from "../utils/log";

/** 处理收到数据 */
export var BleDataHandler = {
  callbacks: Array<BleDataCallback>(),
  init() {
    wx.onBLECharacteristicValueChange(res => {
      loge("收到数据:" + ab2hex(res.value))
      logv("收到数据:" + res.serviceId)
      this._handlerData(res);
    })
  },
  addCallbacks(callback: BleDataCallback) {
    if (this.callbacks.indexOf(callback) == -1) {
      this.callbacks.push(callback);
    }
  },
  removeCallbacks(callback: BleDataCallback) {
    var index = this.callbacks.indexOf(callback);
    if (index != -1) {
      this.callbacks.splice(index, 1);
    }
  },
  _handlerData(res: WechatMiniprogram.OnBLECharacteristicValueChangeListenerResult) {
    this._doAction({
      action: function (c) {
        if (c.onReceiveData) {
          c.onReceiveData(res);
        }
      }
    });
  },
  _doAction(obj: { action: (c: BleDataCallback) => void }) {
    this.callbacks.forEach(c => {
      obj.action(c)
    });
  },
}
export interface BleDataCallback {
  onReceiveData?: (res: WechatMiniprogram.OnBLECharacteristicValueChangeListenerResult) => void
}

/todo 后续优化，1.阻塞式发送数据，2.区分设备/
/** 队列式-分包发送数据 */
export var BleSendDataHandler = {
  mtuMap: new Map<string, number>(),
  sendInfoArray: new Array<SendDataTask>(),
  retryNum: 0,
  setMtu(deviceId: string, mtu: number) {
    this.mtuMap.set(deviceId, mtu)
  },
  sendData(deviceId: string, serviceId: string, characteristicId: string, data: Uint8Array): boolean {
    const mtu = this.mtuMap.get(deviceId)
    let realMTU = 20;
    if (mtu != undefined) realMTU = mtu - 3
    const dataLen = data.byteLength;
    const blockCount = Math.floor(dataLen / realMTU);
    let ret = false;
    for (let i = 0; i < blockCount; i++) {
      const mBlockData = new Uint8Array(realMTU);
      mBlockData.set(data.slice(i * realMTU, i * realMTU + mBlockData.length))
      ret = this._addSendData(deviceId, serviceId, characteristicId, mBlockData);
    }
    if (0 != dataLen % realMTU) {
      const noBlockData = new Uint8Array(dataLen % realMTU);
      noBlockData.set(data.slice(dataLen - dataLen % realMTU, dataLen))
      ret = this._addSendData(deviceId, serviceId, characteristicId, noBlockData);
    }
    return ret
  },
  _addSendData(deviceId: string, serviceId: string, characteristicId: string, data: Uint8Array): boolean {
    const sendDataTask = new SendDataTask(deviceId, serviceId.toUpperCase(), characteristicId.toUpperCase(), data)
    this.sendInfoArray.push(sendDataTask)
    if (this.sendInfoArray.length > 1) {
      return true
    }
    const handleCallback = {
      complete: () => {
        this._writeDataToDevice(this.sendInfoArray, handleCallback)
      }
    }
    this._writeDataToDevice(this.sendInfoArray, handleCallback)
    return true
  },
  _writeDataToDevice(dataInfoArray: Array<SendDataTask>, callback: { complete?: Function }) {
    const dataInfo = dataInfoArray.shift()
    if (dataInfo == undefined) {
      if (dataInfoArray.length == 0) return
      callback.complete?.()
      return
    }
    let sendResult = this._sendData(dataInfo);
    if (!sendResult) {
      callback.complete?.()
      return
    } else {
    }
    callback.complete?.()
  },
  _sendData(sendDataTask: SendDataTask): boolean {
    // 发送失败重发三次
    wx.writeBLECharacteristicValue({
      deviceId: sendDataTask.deviceId,
      serviceId: sendDataTask.serviceId,
      characteristicId: sendDataTask.characteristicId,
      value: sendDataTask.data.buffer,
      fail: (err) => {
        this.retryNum++
        if (this.retryNum >= 3) {
          logd("发送失败，重发数据：->" + "\terr=" + JSON.stringify(err) + " retryNum = " + this.retryNum)
          this._sendData(sendDataTask)
        } else {
          this.retryNum = 0;
        }
        loge("发送数据失败：->" + "\terr=" + JSON.stringify(err))
      },
      success: () => {
        logv("发送数据成功：->" + ab2hex(sendDataTask.data.buffer) + " serviceId:" + sendDataTask.serviceId)
      }
    })
    return true
  }
}
class SendDataTask {
  public deviceId: string
  public serviceId: string
  public characteristicId: string
  public data: Uint8Array
  constructor(deviceId: string, serviceId: string, characteristicId: string, data: Uint8Array) {
    this.deviceId = deviceId
    this.serviceId = serviceId
    this.characteristicId = characteristicId
    this.data = data
  }
}