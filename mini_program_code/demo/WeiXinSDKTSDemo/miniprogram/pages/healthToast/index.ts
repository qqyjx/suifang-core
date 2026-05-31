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
    array: ['所有', '久坐', '喝水', '远眺', '运动', '吃药', '看书', '出行', '洗手'],// 目前只有就做和喝水
    index: 0,
    deviceType: '',
  },

  /**
   * 生命周期函数--监听页面加载
   */
  onLoad() {

  },
  // startTime
  // endTime
  // intervalTime
  // valSwitch
  // deviceControl
  // deviceType
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
      deviceType: array[e.detail.value]
    })

  },
  bindSwitchChange(e: any) {
    let self = this;
    self.setData({
      deviceSwitch: e.detail.value
    })
  },

  startTest() {

    let self = this;
    let data = {
      switch: self.data.deviceSwitch ? 'start' : 'stop',
      startTime: self.data.startTime,// 开始时间
      endTime: self.data.endTime,// 结束时间
      intervalTime: self.data.intervalTime,//间隔时间
      deviceControl: 'setup',
      deviceType: self.data.deviceType
    }

    console.log("data==>",data)
    veepooFeature.veepooSendHealthToastFeatureDataManager(data);
  },
  stopTest() {
    let self = this;
    let data = {
      deviceControl: 'read',
    }
    console.log("data=>", data)
    veepooFeature.veepooSendHealthToastFeatureDataManager(data);
  },
  readTest() {
    let self = this;
    let data = {
      deviceControl: 'read',
    }
    veepooFeature.veepooSendHealthToastFeatureDataManager(data);
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