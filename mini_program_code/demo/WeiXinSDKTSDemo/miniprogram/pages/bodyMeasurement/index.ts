// pages/bodyMeasurement/index.js
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
import { dataStorage } from '../../services/dataStorage'

Page({

  /**
   * 页面的初始数据
   */
  data: {
    device: {},
    deviceIdList: []
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
  BodyCompositionTestStartDataManager() {
    veepooFeature.veepooSendBodyCompositionTestStartDataManager();
  },
  BodyCompositionTestStopDataManager() {
    veepooFeature.veepooSendBodyCompositionTestStopDataManager()
  },
  startGetDataId() {
    veepooFeature.veepooSendReadBodyCompositionTestIdDataManager()
  },
  dataIdGetData() {
    let self = this;
    let deviceIdList: any = this.data.deviceIdList[0];
    console.log("deviceIdList=>", deviceIdList)
    let data = {
      dataId: deviceIdList.dataId
    }
    veepooFeature.veepooSendBodyCompositionIdReadDataManager(data)
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log(" ss 监听蓝牙回调=>", e);
      if (e.type == 32) {
        if (e.name == '身体成分检测' || e.name == '根据Id获取身体成分数据') {
          self.setData({
            device: e
          })
          // saveData 已由 services/bleHub.ts 全局自动处理, 这里不再重复.
        } else if (e.name == '身体成分读取测量保存的数据ID') {
          self.setData({
            deviceIdList: e.content
          })
        }
      }

    })
  },

})
