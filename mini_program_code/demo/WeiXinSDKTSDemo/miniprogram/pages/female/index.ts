// pages/female/index.ts
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    value1: '',
    value2: '',
    value3: '',
    date: '',
    YCdate: '',
    BabyDate: ''
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
  inputValue1(e: any) {
    let self = this;
    self.setData({
      value1: e.detail.value
    })
  },
  inputValue2(e: any) {
    let self = this;
    self.setData({
      value2: e.detail.value
    })
  },
  inputValue3(e: any) {
    let self = this;
    self.setData({
      value3: e.detail.value
    })
  },
  sendData() {
    let self = this;
    let data = {
      deviceControl: '01',
      menstruationTime: self.data.date,
      menstruationLength: self.data.value1,
      menstruationInterval: self.data.value2
    }
    console.log("data=>", data)
    veepooFeature.veepooSendFemaleInstructionsDataManager(data)
  },
  sendData2() {
    let self = this;
    let data = {
      deviceControl: '02',
      menstruationTime: self.data.date,
      menstruationLength: self.data.value1,
      menstruationInterval: self.data.value2
    }
    console.log("data=>", data)
    veepooFeature.veepooSendFemaleInstructionsDataManager(data)
  },
  sendYCData() {
    let data = {
      deviceControl: '03',
      menstruationTime: this.data.YCdate
    }
    veepooFeature.veepooSendFemaleInstructionsDataManager(data)
  },
  readData() {
    let data = {
      deviceControl: '05',
    }
    let result = veepooFeature.veepooSendFemaleInstructionsDataManager(data);
    console.log('result==>', result)
  },
  sendBMData() {
    let self = this;
    let data = {
      deviceControl: '04',
      menstruationTime: self.data.date,
      menstruationLength: self.data.value1,
      menstruationInterval: self.data.value2,
      babySex: self.data.value3,
      babyDateBirth: self.data.BabyDate
    }
    console.log("data=>", data)
    veepooFeature.veepooSendFemaleInstructionsDataManager(data)
  },
  bindDateChange: function (e: any) {
    console.log('picker发送选择改变，携带值为', e.detail.value)
    this.setData({
      date: e.detail.value
    })
  },
  bindYCDateChange: function (e: any) {
    console.log('picker发送选择改变，携带值为', e.detail.value)
    this.setData({
      YCdate: e.detail.value
    })
  },
  bindBabyDateChange: function (e: any) {
    console.log('picker发送选择改变，携带值为', e.detail.value)
    this.setData({
      BabyDate: e.detail.value
    })
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("监听蓝牙回调=>", e);
    })
  },
})