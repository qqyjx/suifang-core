// pages/readDailyData/index.js

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
  onLoad() {
    let data = {
      status: true
    }
    veepooBle.veepooWeiXinSDKRawDataShowStatus(data)
  },

  /**
   * 生命周期函数--监听页面初次渲染完成
   */

  onReady() {
    const HrvData: number[] =
      [88, 92, 99, 98, 96, 93, 83, 87, 93, 97, 86, 82, 96, 87, 89, 95, 85, 88, 88, 93, 94, 93, 86, 97, 89, 92, 97, 108, 92, 101, 89, 98, 101, 98, 96, 94, 96, 87, 96, 83, 93, 91, 83, 84, 92, 79, 94, 89, 80, 87, 85, 77, 71, 85, 78, 90, 83, 98, 98, 82, 80, 91, 84, 101, 98, 100, 93, 91, 93, 102, 97, 91, 96, 88, 90, 93, 86, 82, 95, 86, 102, 95, 82, 82, 91, 88, 100, 95, 86, 93, 93, 88, 98, 90, 77, 97, 86, 89, 94, 90];

    let list: number[] = [];
    HrvData.forEach((item) => {
      list.push(item / 10)
    })

    let drawArr = veepooFeature.veepooGetLorentzScatterPlotData(HrvData);
    console.log("洛伦兹散点图==>", drawArr)
    let starIndexs = veepooFeature.veepooGetLorentzScatterPlotStarIndex(HrvData);
    console.log("洛伦兹星级starIndexs==>", starIndexs);
    let similarity = veepooFeature.VeepooGetLorentzScatterPlotSimilarity(HrvData);
    console.log("洛伦兹相似度similarity=>", similarity)
    let score = veepooFeature.VeepooGetHrvHeartHealthScore(HrvData);
    console.log('心脏健康指数score=>', score);

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
      package: 1
    }
    veepooFeature.veepooSendReadDailyDataManager(data);
  },

  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("日常数据 监听蓝牙回调=>", e);
      // 日常数据
      if (e && e.type == 5) {
        self.setData({
          device: e
        })
        console.log("日常数据====》", e.content.reverse())
        if (e && e.type == 5 && e.Progress == 100) {
          let content = e.content.reverse();
          let arr: any = []
          let rr50Array: number[] = [];
          content.forEach((item: any) => {
            let obj = item.bloodPressure
            let date = item.date.split("-");
            // 获取7小时rr50值
            if (Number(date[3]) < 7) {
              rr50Array.push(...item.rr50)
            }
            arr.push(obj)
          });
          console.log('rr50Array==>', rr50Array);
          console.log('rr50Array.length==>', rr50Array.length);

          let drawArr = veepooFeature.veepooGetLorentzScatterPlotData(rr50Array);
          console.log("洛伦兹散点图==>", drawArr)
          let starIndexs = veepooFeature.veepooGetLorentzScatterPlotStarIndex(rr50Array);
          console.log("洛伦兹星级starIndexs==>", starIndexs);
          let similarity = veepooFeature.VeepooGetLorentzScatterPlotSimilarity(rr50Array);
          console.log("洛伦兹相似度similarity=>", similarity)

          // saveData 已由 services/bleHub.ts 全局自动处理 (type=5 Progress=100), 这里不再重复.
          // 页面里 rr50Array/lorentz/starIndex 仅用于本地可视化, 不入库.
          void rr50Array; void drawArr; void starIndexs; void similarity;
        }
      }
    })
  },

})
