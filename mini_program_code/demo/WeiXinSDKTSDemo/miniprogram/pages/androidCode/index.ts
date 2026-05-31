// pages/screenSetup/index.ts

import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    time: 0,
    phone1: 0,
    name: '',
    message: '',
    array: ['微信', 'qq', '新浪微博', 'facebook', '推特', 'flickr', 'Linke', 'WhatsApp', 'Line'],
    index: 2
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
    let self = this;
    this.notifyMonitorValueChange();
    let str = ['E9', '99', '88', 'E6', '98', 'BE', 'E6', '96', '87', 'E8', '81', '94', 'E9', '80', '9A']
    let nameArray: any = []
    console.log
    str.forEach((item: any, index: number) => {
      nameArray.push(self.hexTo10(item));
    })
    console.log('nameArray=>', nameArray)

    console.log("uint8ArrayToNumber=>", this.uint8ArrayToString(nameArray))

  },
  hexTo10(num: any) {
    return parseInt(num, 16);
  },
  bindPickerChange: function (e: any) {
    console.log("e=>", Number(e.detail.value) + 2)
    let array = this.data.array
    this.setData({
      index: Number(e.detail.value) + 2,
      deviceType: array[e.detail.value]
    })

  },
  uint8ArrayToString(bytes: any) {
    // 小程序不支持以下写法
    // const decoder = new TextDecoder();
    // const str = decoder.decode(new Uint8Array(bytes));
    const str = decodeURIComponent(escape(String.fromCharCode(...bytes)));
    return str
  },
  uint8ArrayToNumber(num: any) {
    const view = String.fromCharCode(num);
    return view
  },
  stringToUtf8Array(str: any) {
    const view = unescape(encodeURIComponent(str)).split("").map(val => val.charCodeAt());
    return view
  },
  inputValue1(e: any) {
    console.log("e=>", e)
    this.setData({
      phone1: e.detail.value
    })
  },
  inputValue2(e: any) {
    console.log("e=>", e)
    this.setData({
      message: e.detail.value
    })
  },
  inputValue3(e: any) {
    console.log("e=>", e)
    this.setData({
      name: e.detail.value
    })
  },
  startPhone(e: any) {
    let self = this;
    let data = {
      type: '02',
      phone: self.data.phone1
    }
    veepooFeature.veepooSendAndroidCodeDataManager(data);
  },

  storagePhone(e: any) {
    let self = this;
    let data = {
      type: '01',
      phone: self.data.phone1,
      name: self.data.name
    }
    veepooFeature.veepooSendAndroidCodeDataManager(data);
  },

  // 

  sendSMS() {
    let self = this;
    let data = {
      type: '04',
      phone: self.data.phone1,
      message: self.data.message
    }
    veepooFeature.veepooSendAndroidCodeDataManager(data);
  },

  sendSMS2() {
    let self = this;
    let data = {
      type: '03',
      phone: self.data.phone1,
      message: self.data.message,
      name: self.data.name
    }

    veepooFeature.veepooSendAndroidCodeDataManager(data);
  },


  sendSMS3() {
    let self = this;
    console.log("self.data.index=>", self.data.index);
    let apply = self.data.index.toString(16).padStart(2, '0');
    console.log("apply=>", apply)
    let data = {
      type: '05',
      apply: self.data.index,
      message: self.data.message,
    }

    veepooFeature.veepooSendAndroidCodeDataManager(data);
  },
  readLightUpTimeData(e: any) {


    let data = {
      switch: 'read',
      duration: this.data.time
    }
    veepooFeature.veepooSendLightUpTimeDataManager(data);
  },


  // veepooBle
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log(" step 监听蓝牙回调=>", e);

    })
  },

  /**
   * 生命周期函数--监听页面隐藏
   */
  onHide() {

  },

  /**
   * 生命周期函数--监听页面卸载
   */
  onUnload() {

  },

  /**
   * 页面相关事件处理函数--监听用户下拉动作
   */
  onPullDownRefresh() {

  },

  /**
   * 页面上拉触底事件的处理函数
   */
  onReachBottom() {

  },

  /**
   * 用户点击右上角分享
   */
  onShareAppMessage() {

  }
})