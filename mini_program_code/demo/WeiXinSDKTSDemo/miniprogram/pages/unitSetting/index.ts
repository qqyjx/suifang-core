// pages/unitSetting/index.js
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    unitLength: '公制(米,千米)', // 长度 1，表示公制(默认) 2，表示英制
    unitBodyTemperature: '摄氏度', // 体温 1 摄氏度  2 华氏度
    unitBloodSugar: 'mmol/L', // 血糖 1 mmol/L  2 mg/dl
    unitUricAcid: 'μmol/L', // 尿酸 1 μmol/L  2 mg/dl
    unitBloodLipid: 'mmol/L', // 血脂 1 mmol/L  2 mg/dl
    array1: ['公制(米,千米)', '英制(英尺,英里)'],
    array2: ['摄氏度', '华氏度'],
    array3: ['mmol/L', 'mg/dl'],
    array4: ['μmol/L', 'mg/dl'],
    array5: ['mmol/L', 'mg/dl']
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

    this.unitReadData()
  },
  bindPickerChange1: function (e: any) {
    let index = Number(e.detail.value);

    this.setData({
      unitLength: this.data.array1[index]
    })
  },
  bindPickerChange2: function (e: any) {
    let index = Number(e.detail.value);

    this.setData({
      unitBodyTemperature: this.data.array2[index]
    })
  },
  bindPickerChange3: function (e: any) {
    let index = Number(e.detail.value);

    this.setData({
      unitBloodSugar: this.data.array3[index]
    })
  },
  bindPickerChange4: function (e: any) {
    let index = Number(e.detail.value);

    this.setData({
      unitUricAcid: this.data.array4[index]
    })
  },
  bindPickerChange5: function (e: any) {
    let index = Number(e.detail.value);

    this.setData({
      unitBloodLipid: this.data.array5[index]
    })
  },

  settingData() {
    let unitLength = this.data.unitLength == '公制(米,千米)' ? 'metricSystem' : 'english';
    let unitBodyTemperature = this.data.unitBodyTemperature == '摄氏度' ? 'degreeCelsius' : 'fahrenheit';
    let unitBloodSugar = this.data.unitBloodSugar;
    let unitUricAcid = this.data.unitUricAcid;
    let unitBloodLipid = this.data.unitBloodLipid;
    // 参数
    let data = {
      unitLength,
      unitBodyTemperature,
      unitBloodSugar,
      unitUricAcid,
      unitBloodLipid,
    }
    veepooFeature.veepooSendUnitSettingDataManager(data);
  },
  unitReadData() {
    veepooFeature.veepooSendReadDeviceUnitSettingDataManager();
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log(" 单位设置 监听蓝牙回调=>", e);
      if (e.type == 11) {
        if (e.settingStatus) {
          wx.showToast({
            title: '设置成功',
            icon: 'none'
          })
        }

      }

    })
  },
});