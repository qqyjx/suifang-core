
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index';
// pages/function_test/dial_operate/dial_add_background/index.ts
Page({

  /**
   * 页面的初始数据
   */
  data: {
    isTransfering: false,
    transferProgressText: "",
    imgs: "../../../image/dial_icon3_main.png"
  },
  devScreenWidth: 240,
  devScreenHeight: 280,
  /**
   * 生命周期函数--监听页面加载
   */
  onLoad(options) {



  },
  onUnload() {
  },
  saveFile(fs: WechatMiniprogram.FileSystemManager, filePath: string, data: Uint8Array) {
    fs.writeFileSync(filePath, data.buffer)
    console.log("writeFileSync : ", data);
    console.log("filePath : ", filePath);

    wx.saveImageToPhotosAlbum({
      filePath: filePath,
      success() {
        wx.showToast({
          title: '保存成功'
        })
      },
      fail() {
        wx.showToast({
          title: '保存失败',
          icon: 'none'
        })
      }
    })
  },
  onShow() {
    // this.notifyMonitorValueChange()
  },
  uint8ArrayToHex(uInt8Array: any) {
    return uInt8Array.map((byte: any) => byte.toString(16).padStart(2, '0')).join('');
  },
  //裁剪图片
  clickWay1() {
    let self = this;
    wx.chooseMedia({
      count: 1,
      mediaType: ['image'],
      sizeType: ['original'],
      success: (res) => {
        console.log("选择图片", res);
        const imagePath = res.tempFiles[0].tempFilePath
        wx.navigateTo({
          url: '/pages/dialBackground/background_image/index?imagePath=' + imagePath + "&width=" + this.devScreenWidth + "&height=" + this.devScreenHeight,
          events: {
            onDialBgData: (resDialBg: { data: any }) => {
              console.log("resDialBg==+>", resDialBg);
              this._handleDialBgData(resDialBg.data);
            }
          }
        })
      }
    })
    return
  },//传输资源文件

  clickCancelTransfer() {

  },

  // 发送缩略图
  _handleDialBgData(data: any) {
    let self = this;
    console.log("onDialBgData :  ", data);

    let value = {
      addr: 0,
      data: data,
      surplusLength: 225280
    }
    console.log("value==", value)
    veepooFeature.veepooStartTransferManager(value, function (e: any) {
      console.log("e===>", e)
    })
  },
  // veepooWeiXinSDKUpdateDeviceDialServiceManager
  // veepooWeiXinSDKNotifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("监听蓝牙回调=>", e);
    })
  },
})