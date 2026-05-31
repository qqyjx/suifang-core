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
    this.notifyMonitorValueChange()
  },
  braceletSearch(){
    console.log('aaa')
    veepooFeature.veepooSendFindYourPhoneDataManager(function(e:any){
      console.log("e=>",e)
    });
  },
  startBraceletSearch(){
    let data = {
      switch:'start'
    }
    veepooFeature.veepooSendPhoneLookBraceletDataManager(data,function(e:any){
      console.log("e=>",e)
    });
  },
  stopBraceletSearch(){
    let data = {
      switch:'stop'
    }
    veepooFeature.veepooSendPhoneLookBraceletDataManager(data,function(e:any){
      console.log("e=>",e)
    });
  },
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e:any) {
      console.log(" 监听蓝牙回调=>", e);
    })
  },
})