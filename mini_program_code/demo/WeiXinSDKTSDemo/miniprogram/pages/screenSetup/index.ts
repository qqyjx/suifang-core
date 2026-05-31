// pages/screenSetup/index.ts

import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    time: 0
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
  inputValue(e: any) {
    console.log("e=>", e)
    this.setData({
      time: e.detail.value
    })
  },
  LightUpTimeData(e: any) {


    let data = {
      switch: 'setup',
      duration: this.data.time
    }
    veepooFeature.veepooSendLightUpTimeDataManager(data);
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