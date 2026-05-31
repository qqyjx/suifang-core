// pages/personalInfo/index.js
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {

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
    this.notifyMonitorValueChange();
    let value = 123;
    let val: any = ''
    if (typeof value != 'string') {
      val = String(value)
    } else {
      val = value
    }

    let hex = value.toString(16);
  },
  onSubmit(e: any) {
    let info = e.detail.value;
    console.log(info);
    if (!info.height || !info.weight || !info.age || !info.sex || !info.steps || !info.sleep) {
      wx.showToast({
        title: '请输入完成相应值在提交！',
        icon: 'none'
      })
      return
    }

    let data = {
      height: info.height,
      weight: info.weight,
      age: info.age,
      sex: info.sex,
      steps: info.steps,
      sleep: info.sleep
    }
    veepooFeature.veepooSynchronizingPersonalInformationManager(data);
  },
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("同步个人信息 监听蓝牙回调=>", e);
      if (e.content.settingState) {
        wx.showToast({
          title: '同步成功',
          icon: 'none'
        })
      }
      if (e) {
        self.setData({
          device: e
        })
      }
    })
  },

})