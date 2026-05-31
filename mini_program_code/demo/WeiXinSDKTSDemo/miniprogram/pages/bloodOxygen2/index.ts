// pages/universalBlood/index.ts
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    device: null
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

  startTest() {
    let self = this;
    let data = {
      switch: 'start',
    }
    veepooFeature.veepooSendBloodOxygenControlDataManager(data);
  },
  stopTest() {
    let self = this;
    let data = {
      switch: 'stop',
    }
    veepooFeature.veepooSendBloodOxygenControlDataManager(data);
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("拍照 监听蓝牙回调=>", e);
      self.setData({
        device: e
      })

    })
  },
})