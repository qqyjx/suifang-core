import { PackResFormat, Uint8ArrayToString } from "../../../jieli_sdk/jl_lib/jl_packResFormat_1.0.0";
import { RCSPOpSystemInfo, RCSPOpWatchDial } from "../../../jieli_sdk/lib/rcsp-impl/rcsp";
import { OPDirectoryBrowse, OPLargerFileTrans } from "../../../jieli_sdk/jl_lib/jl-rcsp-op/jl_op_watch_1.1.0";

import { veepooJLGetFileDataManager,veepooJLAddDialTransferStartManager } from '../../../jieli_sdk/index'

Page({
  /**
   * 页面的初始数据
   */
  data: {
    fileName: '',
    fileInfo: '',
    fileStatus: 0,//0：未读取，1：读取中，2：已读取
    transferProgressText: '',
    isTransfering: false,
    fileData:{}
  },
  dialData: Uint8Array.prototype,
  lastModifyTime: 0,
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

  },

  /**
   * 生命周期函数--监听页面隐藏
   */
  onHide() {

  },

  onUnload() {
    RCSPOpWatchDial?.cancelAddWatchResourseFile()
  },
  /**
  * 点击事件--输入文件名
  */
  inputFileNmae(e: any) {
    var value = e.detail.value
    console.log(" tas " + value);
    this.setData({
      fileName: value
    })
  },
  /**
  * 点击事件--读取表盘文件
  */
  clickReadDialFile() {
    let self = this;
    if (this.data.isTransfering) {
      return
    }
    wx.chooseMessageFile({
      count: 1,
      success: (res) => {
        const tempFilePaths = res.tempFiles[0];
        console.log("res==>",res)
        console.log("tempFilePaths : ", tempFilePaths);
        // 获取文件数据
        veepooJLGetFileDataManager(tempFilePaths, function (result: object) {
          console.log("result获取文件成功=>", result)
          self.setData({
            fileData:result
          })
        })

      }
    })
  },
  /**
  * 点击事件--开始传输
  */
  clickStartTransferDialFile() {
    let self = this;
    const fileName = this.data.fileName.trim()
    let fileData:any = this.data.fileData;
    if (fileData.fileName.length == 0) {
      wx.showToast({ title: "文件名不能为空", icon: 'error' })
      return
    }

    console.log("点击了开始传输")

    veepooJLAddDialTransferStartManager(fileData,function(result:any){
      console.log("传输进度result=>",result);
      self.setData({
        transferProgressText:result.transferProgressText
      })
    })
    // RCSPOpSystemInfo?.getSystemInfo().then((systemInfo) => {
    //   console.log("开始传输执行")
    //   console.log("systemInfo=>", systemInfo)
    //   if (systemInfo.callStatus == 1) {//手表通话中
    //     wx.showToast({
    //       title: '通话中，不可操作',
    //       icon: 'none'
    //     })
    //   } else if (systemInfo.callStatus == 0) {//手表空闲
    //     if (this.data.fileStatus == 2 && this.dialData.length > 0) {
    //       const _callback: OPLargerFileTrans.TransferTaskCallback = {
    //         onError: (code: number) => {
    //           this.setData({
    //             transferProgressText: "传输失败，code:" + code
    //           })
    //         },
    //         onStart: () => {
    //           this.setData({
    //             transferProgressText: "开始传输",
    //             isTransfering: true
    //           })
    //         },
    //         onProgress: (progress: number) => {
    //           this.setData({
    //             transferProgressText: "正在传输，进度:" + progress
    //           })
    //         },
    //         onSuccess: () => {
    //           this.setData({
    //             transferProgressText: "传输成功",
    //             isTransfering: false
    //           })
    //         },
    //         onCancel: (_code: number) => {
    //           this.setData({
    //             transferProgressText: "传输取消",
    //             isTransfering: false
    //           })
    //         }
    //       }
    //       RCSPOpWatchDial?.addWatchResourseFile(this.dialData, fileName, this.lastModifyTime, true, _callback).then((res) => {
    //         if (res instanceof OPDirectoryBrowse.File) {//传输成功且刷新目录
    //           //设置为当前表盘
    //           RCSPOpWatchDial?.setUsingDial(res)
    //         } else if (res == undefined) {//传输完成后,目录浏览找不到该文件
    //           console.error("传输完成后找不到该文件，可能是文件名不符合标准，太长或者带中文");
    //         } else {//传输成功且不刷新目录
    //           console.log("目录浏览的相对路径：path：" + res);
    //         }
    //       }).catch((error) => {
    //         wx.showToast({ title: "添加表盘失败，" + error, icon: 'error' })
    //       })
    //     } else {
    //       wx.showToast({ title: "请先读取表盘文件", icon: 'error' })
    //       return
    //     }
    //   }
    // })
  },
  /**
  * 点击事件--取消传输
  */
  clickCancelTransferDialFile() {
    RCSPOpWatchDial?.cancelAddWatchResourseFile()
  }
})