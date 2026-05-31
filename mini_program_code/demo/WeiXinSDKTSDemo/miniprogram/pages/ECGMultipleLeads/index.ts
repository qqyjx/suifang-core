// pages/ecgTest/index.js

import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
import { ab2hex } from '../../utils/util'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    device: {}
  },

  /**
   * 生命周期函数--监听页面加载
   */
  onLoad(options) {

  },

  /**
   * 生命周期函数--监听页面初次渲染完成
   */
  onReady() {

  },

  /**
   * 生命周期函数--监听页面显示
   */
  onShow() {
    this.notifyMonitorValueChange();

    let bleDate = wx.getStorageSync('bleDate');
    // let bleDevice = {
    //   deviceId: bleDate.deviceId,
    //   serviceId: 'F0020001-0451-4000-B000-000000000000',
    //   notifyCharacteristicId: 'F0020002-0451-4000-B000-000000000000',
    //   writeCharacteristicId: 'F0020003-0451-4000-B000-000000000000',
    // }

    // wx.notifyBLECharacteristicValueChange({
    //   state: true, // 启用 notify 功能
    //   deviceId: bleDevice.deviceId,
    //   serviceId: bleDevice.serviceId,
    //   characteristicId: bleDevice.notifyCharacteristicId,
    //   success(res) {
    //     wx.onBLECharacteristicValueChange(function (res) {
    //       // 获取蓝牙设备返回的数据
    //       var value = ab2hex(res.value);
    //       console.log("value======>", value)
    //     })
    //   },
    //   fail(err) { }
    // })
  },
  // 无参数
  MultipleLeadsStart() {
    let data = {
      switch: 'start'
    }
    veepooFeature.veepooSendMultipleLeadsDataManager(data);
  },
  MultipleLeadsStop() {
    let data = {
      switch: 'stop'
    }
    veepooFeature.veepooSendMultipleLeadsDataManager(data);
  },

  // 监听订阅 veepooWeiXinSDKUpdateECGServiceManager   ecg服务
  notifyMonitorValueChange() {
    let self = this;
    // ecg多导服务
    veepooBle.veepooWeiXinSDKUpdateECGServiceManager(function (e: any) {
      console.log(" ECG 监听蓝牙回调=>", e);
    })

    // 设备服务
    // veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
    //   console.log(" ECG 监听蓝牙回调=>", e);
    // })
  },
})