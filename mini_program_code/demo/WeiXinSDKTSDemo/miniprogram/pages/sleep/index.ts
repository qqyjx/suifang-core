// pages/sleep/index.js

import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
import { dataStorage } from '../../services/dataStorage'

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
  },
  ReadPreciseSleepManager(e:any) {
    let index = e.currentTarget.dataset.index
    let data = {
      day: index
    }
    veepooFeature.veepooSendReadPreciseSleepManager(data)
  },

  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e:any) {
      console.log(" 睡眠 监听蓝牙回调=>", e);
      if (e) {
        if (e.name == '精准睡眠数据') {
          self.setData({
            device: e
          })
          // saveData 已由 services/bleHub.ts 全局自动处理, 这里不再重复.
        }
      }


    })
  },
})
