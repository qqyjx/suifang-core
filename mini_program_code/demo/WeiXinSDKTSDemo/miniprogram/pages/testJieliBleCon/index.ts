// index.ts
// 获取应用实例
import { RCSP, RCSPManager, RCSPOpWatchDial } from "../../jieli_sdk/lib/rcsp-impl/rcsp"
import { DeviceManager, DeviceBluetooth } from "../../jieli_sdk/lib/rcsp-impl/dev-bluetooth";
import { BluetoothDevice } from "../../jieli_sdk/lib/rcsp-protocol/rcsp-util";
import { wxBluetooth } from "../../jieli_sdk/lib/rcsp-impl/bluetooth";
Page({
  data: {
    isScaning: false,
    scanDevices: new Array(),
    triggered: false,
    filterDevName: '',
    connectedDeviceId: ""
  },
  _freshing: false,
  _lastUpdateTime: 0,
  _foundSrcDevlist: new Array(),
  _RCSPWrapperEventCallback: RCSP.RCSPWrapperEventCallback.prototype,
  onLoad() {
    console.log("RCSPManager=>", RCSPManager)
    const value = wx.getStorageSync('filterDevName')
    if (value) {
      this.setData({ filterDevName: value })
    }
    {
      DeviceManager.observe(this._onRCSPBluetoothEvent)
    }
    {
      this._RCSPWrapperEventCallback = new RCSP.RCSPWrapperEventCallback()
      this._RCSPWrapperEventCallback.onEvent = (event) => {
        if (event.type === "onSwitchUseDevice") {
          const connectedDeviceId = event.onSwitchUseDeviceEvent?.device?.deviceId
          console.log(" onSwitchUseDevice111: " + connectedDeviceId);
          this.setData({
            connectedDeviceId: connectedDeviceId == undefined ? "" : connectedDeviceId
          })
          if (connectedDeviceId != undefined) {
            // setTimeout(() => {
            //   wx.redirectTo({
            //     url: '/pages/function_test/index'
            //   })
            // }, 300);
          }
        }
      }
      RCSPManager.observe(this._RCSPWrapperEventCallback)
      this.setData({
        connectedDeviceId: RCSPManager.getCurrentRcspOperateWrapper()?.deviceId
      })
    }
    setTimeout(() => {
      DeviceManager.starScan()
    }, 500);
  },
  onUnload() {
    DeviceManager.removeObserve(this._onRCSPBluetoothEvent)
    RCSPManager.removeObserve(this._RCSPWrapperEventCallback)
  },
  isAdapter: false,
  onRefresh() {
    // if (!this.isAdapter) {
    //   console.log("openBluetoothAdapter ");
    //   wx.openBluetoothAdapter({
    //     success (res) {
    //       console.log("success")
    //     },fail(){
    //       console.log("fail")
    //     }
    //   })
    //   return
    // }
    if (wxBluetooth.bluetoothManager.isBluetoothAdapterAvailable() == false) {
      this.setData({
        triggered: false,
      })
      console.log("打开蓝牙适配器");
      wxBluetooth.bluetoothManager.openBluetoothAdapter({
        success: () => {
          setTimeout(() => {
            this.onRefresh()
          }, 100);
        }, fail: () => {
          wx.showToast({ title: "蓝牙适配器异常" })
        }
      })
      return
    }
    console.log("onRefresh");
    if (this._freshing) return
    console.log("onRefresh111");
    this._freshing = true
    setTimeout(() => {
      this.setData({
        triggered: false,
      })
      this._freshing = false
    }, 500)
    if (DeviceManager.isScanning()) {
      // RCSPBluetooth.bleScan.refreshScan()
      DeviceManager.refreshScan()
      this.setData({
        triggered: false,
      })
      this.discoveryDevList = new Array()
      this.setData({ scanDevices: new Array() })
      this.setData({ isScaning: true })
      console.log("onRefresh222");
      return
    }
    DeviceManager.starScan()
  },
  clickDial() {
    wx.navigateTo({
      url: '/pages/function_test/dial_operate/dial_add_dial/index'
    })
  },
  onSetFilter() {
    wx.showModal({
      title: "设备过滤条件",
      editable: true,
      content: this.data.filterDevName,
      success: (res) => {
        if (res.confirm) {
          this.setData({
            filterDevName: res.content
          })
          this._filterDevName(this._foundSrcDevlist)
          wx.setStorageSync('filterDevName', res.content)
        } else if (res.cancel) {
        }
      }
    })
  },
  onConnectDev(e: any) {
    console.log("e device: ", e);
    const dev = e.currentTarget.dataset.dev
    const device = dev.device as BluetoothDevice
    const rcspOperateWrapper = RCSPManager.getCurrentRcspOperateWrapper();
    console.log("rcspOperateWrapper====================>", rcspOperateWrapper)
    if (rcspOperateWrapper == undefined) {//有已连接设备
      DeviceManager.connecDevice(device)
    } else {
      if (rcspOperateWrapper.deviceId == dev.device.deviceId) {
        wx.showActionSheet({
          itemList: ['断开设备', '控制设备'],
          success: (res) => {
            if (res.tapIndex == 0) {//断开设备
              DeviceManager.disconnectDevice(device)
            } else if (res.tapIndex == 1) {//控制设备
              wx.redirectTo({
                url: '/pages/function_test/index'
              })
            }
          }
        })
      } else {
        wx.showToast({ title: "请先断开已连接设备" })
      }
    }
  },
  cacheScanDevice: new Map(),
  discoveryDevList: Array(),//发现设备
  _onRCSPBluetoothEvent(event: DeviceBluetooth.DeviceBluetoothEvent) {
    if (event.type === 'onDiscoveryStatus') {
      if (event.onDiscoveryStatusEvent) {
        const bStart = event.onDiscoveryStatusEvent.bStart
        if (bStart) {//开始扫描
          this.discoveryDevList = new Array()
          this.setData({ scanDevices: new Array() })
          this.setData({ isScaning: true })
          wx.showToast({
            title: "扫描开始",
            icon: "none"
          })
        } else {//扫描结束
          this.setData({ isScaning: false })
          wx.showToast({
            title: "扫描结束",
            icon: "none"
          })
        }
      }
    } else if (event.type === 'onDiscovery') {
      if (event.onDiscoveryEvent) {
        const device = event.onDiscoveryEvent.device
        const bleScanMassage = event.onDiscoveryEvent.bleScanMessage
        if (device.name === "") {
          return
        }
        let isContain = false
        for (let index = 0; index < this.discoveryDevList.length; index++) {
          const element = this.discoveryDevList[index];
          if (element.deviceId === device.deviceId) {
            isContain = true
            break;
          }
        }
        if (!isContain) {
          // const tempDeviceArray = this.data.scanDevices
          // const tempDev = { id: tempDeviceArray.length, name: device.name, scanMassage: bleScanMassage, device: device }
          // tempDeviceArray.push(tempDev)
          this.discoveryDevList.push({
            deviceId: device.deviceId,
            device: device,
            bleScanMassage: bleScanMassage
          })
          // this.setData({
          //   scanDevices: tempDeviceArray
          // })
          this._onFoundDevice(this.discoveryDevList)
        }
      }
    }
  },
  _onFoundDevice(devs: any[]) {
    const tempList = new Array()
    devs.forEach(element => {
      const devName = element.device.name
      const time = (new Date()).getTime()
      if (devName && devName !== '') {
        let cacheObj = this.cacheScanDevice.get(element.deviceId)
        if (cacheObj != undefined) {
          if (time - cacheObj.time > 2000) {//2s更新
            cacheObj.element = element
            cacheObj.time = time
            this.cacheScanDevice.set(element.deviceId, cacheObj)
          }
          tempList.push(cacheObj.element)
        } else {
          cacheObj = {}
          cacheObj.element = element
          cacheObj.time = time
          this.cacheScanDevice.set(element.deviceId, cacheObj)
          tempList.push(cacheObj.element)
        }
      }
    });
    tempList.sort(function (a, b) { return b.device.RSSI - a.device.RSSI })
    this._foundSrcDevlist = tempList
    this._filterDevName(this._foundSrcDevlist)
  },
  _filterDevName(devs: any[]) {
    const filterDevName = this.data.filterDevName.toLowerCase()
    const tempList = new Array()
    devs.forEach(e => {
      const devName = e.device.name?.toLowerCase()
      if (devName && devName.includes(filterDevName)) {
        tempList.push(e)
      }
    })
    this.setData({ scanDevices: tempList })
  }
})
