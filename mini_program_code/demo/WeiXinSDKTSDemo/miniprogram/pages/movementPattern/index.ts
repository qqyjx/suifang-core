// pages/movementPattern/index.ts
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    crcData: {},
    exerciseData:{}
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
  startMovementPatternD5DataManager() {
    let value = {
      switch: 'start'
    }
    veepooFeature.veepooSendAppStartMovementPatternD5DataManager(value);
  },
  stopMovementPatternD5DataManager() {
    let value = {
      switch: 'stop'
    }
    veepooFeature.veepooSendAppStartMovementPatternD5DataManager(value);
  },
  readMovementPatternD5DataManager() {
    let value = {
      switch: 'read'
    }
    veepooFeature.veepooSendAppStartMovementPatternD5DataManager(value);
  },
  startMovementPatternD3DataManager() {
    veepooFeature.veepooSendAppStartMovementPatternD3DataManager();
  },
  readMovementPatternD34DataManager() {
    let data = {
      module: 1   //  1 2 3 模块，共三个
    }
    veepooFeature.veepooSendReadMovementPatternD4DataManager(data);
  },
  readMovementPatternD34DataManager2() {
    let data = {
      module: 2   //  1 2 3 模块，共三个
    }
    veepooFeature.veepooSendReadMovementPatternD4DataManager(data);
  },
  readMovementPatternD34DataManager3() {
    let data = {
      module: 3   //  1 2 3 模块，共三个
    }
    veepooFeature.veepooSendReadMovementPatternD4DataManager(data);
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("睡眠 监听蓝牙回调=>", e);
      if (e.type == 14) {
        self.setData({
          crcData: e.content
        })
      }else if(e.type == 16){
        self.setData({
          exerciseData:e.content
        })
      }
    })
  },
})