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
    intervalTime: '30'
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

  startTest() {
    let self = this;
    let data = {
      switch: 'start',
      startTime: self.data.startTime,// 开始时间
      endTime: self.data.endTime,// 结束时间
      intervalTime: self.data.intervalTime//间隔时间
    }
    if (!data.startTime) {
      wx.showToast({
        title: '请添加开始时间',
        icon: 'none'
      })
      return
    } else if (!data.endTime) {
      wx.showToast({
        title: '请添加结束时间',
        icon: 'none'
      })
      return
    }
    console.log("data=>", data)
    veepooFeature.veepooSendSetupSedentaryToastTimeDataManager(data);
  },
  stopTest() {
    let self = this;
    let data = {
      switch: 'stop',
      startTime: self.data.startTime,// 开始时间
      endTime: self.data.endTime,// 结束时间
      intervalTime: self.data.intervalTime//间隔时间
    }
    if (!data.startTime) {
      wx.showToast({
        title: '请添加开始时间',
        icon: 'none'
      })
      return
    } else if (!data.endTime) {
      wx.showToast({
        title: '请添加结束时间',
        icon: 'none'
      })
      return
    }
    console.log("data=>", data)
    veepooFeature.veepooSendSetupSedentaryToastTimeDataManager(data);
  },
  readTest() {
    let self = this;
    let data = {
      switch: 'read',
    }

    console.log("data=====", data)
    veepooFeature.veepooSendSetupSedentaryToastTimeDataManager(data);
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("监听蓝牙回调=>", e);
      let content = e.content;

      if (e.type == 23) {
        self.setData({
          startTime: content.startTime,
          endTime: content.endTime,
          intervalTime: content.intervalTime,
          deviceSwitch: content.deviceControl
        })
        console.log("content=>", content)
        console.log("endTime=>", self.data.endTime)
      }

    })
  },
})