
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
  readData(e: any) {
    let index = e.currentTarget.dataset.index
    let data = {
      day: index, // 0 今天  1 昨天 2 前天
      package: 1,// 读取报数，从第一个开始
    }
    console.log("data==>",data)
    veepooFeature.veepooReadAutoTemperatureMeasurementDataManager(data);
  },
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log(" ss 监听蓝牙回调=>", e);
      if (e.name = '体温自动检测读取') {
        self.setData({
          device: e
        })
      } else if (e.name == '体温自动检测') {
        self.setData({
          device: e
        })
      }

    })
  },
})