// pages/universalBlood/index.ts
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    startTime: '00:00',
    endTime: '00:00',
    deviceSwitch: false,
    intervalTime: '30',
    deviceLevel: 5,
    array: [1, 2, 3, 4, 5, 6, 7, 8, 9]
  },

  /**
   * 生命周期函数--监听页面加载
   */
  onLoad() {

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
    this.notifyMonitorValueChange()
  },
  bindStartTimeChange: function (e: any) {
    console.log('picker发送选择改变，携带值为', e.detail.value)
    this.setData({
      startTime: e.detail.value
    })
  },
  bindStopTimeChange: function (e: any) {
    console.log('picker发送选择改变，携带值为', e.detail.value)
    this.setData({
      endTime: e.detail.value
    })
  },
  changeIntervalTime(e: any) {
    let self = this;
    self.setData({
      intervalTime: e.detail.value
    })
  },
  bindPickerChange: function (e: any) {
    console.log("e=>", e.detail.value)
    let array = this.data.array
    this.setData({
      index: e.detail.value,
      deviceLevel: array[e.detail.value]
    })

  },

  startTest() {
    let self = this;
    let data = {
      switch: 'start',
      startTime: self.data.startTime,// 开始时间
      endTime: self.data.endTime,// 结束时间
      deviceLevel: self.data.deviceLevel
    }
    console.log("data==>", data)
    veepooFeature.veepooSendTurnWristBrightScreenDataManger(data);
  },
  stopTest() {
    let self = this;
    let data = {
      switch: 'stop',
      startTime: self.data.startTime,// 开始时间
      endTime: self.data.endTime,// 结束时间
      deviceLevel: self.data.deviceLevel
    }
    veepooFeature.veepooSendTurnWristBrightScreenDataManger(data);
  },
  readTest() {
    let self = this;
    let data = {
      switch: 'read',
      startTime: self.data.startTime,// 开始时间
      endTime: self.data.endTime,// 结束时间
      deviceLevel: self.data.deviceLevel
    }
    console.log("data=>", data)
    veepooFeature.veepooSendTurnWristBrightScreenDataManger(data);
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("监听蓝牙回调=>", e);

      if (e.type == 25) {
        self.setData({
          startTime: e.content.startTime,
          endTime: e.content.endTime,
          deviceLevel: e.content.deviceLevel,
        })
      }
    })
  },
})