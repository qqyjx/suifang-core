// pages/universalBlood/index.ts
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
Page({

  /**
   * 页面的初始数据
   */
  data: {
    datas: {
      VPSettingCall: 'start',
      VPSettingSMS: 'stop',
      VPSettingWechat: 'stop',
      VPSettingQQ: 'stop',
      VPSettingSina: 'stop',
      VPSettingFaceBook: 'stop',
      VPSettingTwitter: 'stop',
      VPSettingFlickr: 'stop',
      VPSettingLinkedln: 'stop',
      VPSettingwhatsapp: 'stop',
      VPSettingLine: 'stop',
      VPSettingInstagram: 'stop',
      VPSettingSnapchat: 'stop',
      VPSettingSkype: 'stop',
      VPSettingGMail: 'stop',
      VPSettingDingTalk: 'stop',
      VPSettingWeChatWork: 'stop',
      VPSettingOtherTikTok: 'stop',
      VPSettingOtherTelegram: 'stop',
      VPSettingOtherConnected2: 'stop',
      VPSettingKakaoTalk: 'stop',
      VPSettingJingYou: 'stop',
      VPSettingMessenger: 'stop',
      VPSettingOthers:'stop'
    },
    toastList: [
      {
        name: '来电',
        bindTap: 'bindLaidian',
        status: false,
      },
      {
        name: '短信',
        bindTap: 'bindDuanxing',
        status: false
      },
      {
        name: '微信',
        bindTap: 'bindWechat',
        status: false
      },
      {
        name: 'QQ',
        bindTap: 'bindQQ',
        status: false
      },
      {
        name: 'Sina',
        bindTap: 'bindSina',
        status: false
      },
      {
        name: 'FaceBook',
        bindTap: 'bindFaceBook',
        status: false
      },
      {
        name: 'Twitter',
        bindTap: 'bindTwitter',
        status: false
      },
      {
        name: 'Flickr',
        bindTap: 'bindFlickr',
        status: false
      },
      {
        name: 'Linkedln',
        bindTap: 'bindLinkedln',
        status: false
      },
      {
        name: 'whatsapp',
        bindTap: 'bindWhatsapp',
        status: false
      },
      {
        name: 'Line',
        bindTap: 'bindLine',
        status: false
      },
      {
        name: 'Instagram',
        bindTap: 'bindInstagram',
        status: false
      },
      {
        name: 'Snapchat',
        bindTap: 'bindSnapchat',
        status: false
      },
      {
        name: 'Skype',
        bindTap: 'bindSkype',
        status: false
      },
      {
        name: 'Gmail',
        bindTap: 'bindGmail',
        status: false
      },
      {
        name: '钉钉',
        bindTap: 'bindDingDing',
        status: false
      },
      {
        name: '企业微信',
        bindTap: 'bindQiyeweixin',
        status: false
      },
      {
        name: 'tiktok',
        bindTap: 'bindTiktok',
        status: false
      },
      {
        name: 'telegram',
        bindTap: 'bindTelegram',
        status: false
      },
      {
        name: 'connected2',
        bindTap: 'bindConnected2',
        status: false
      },
      {
        name: 'KakaoTalk',
        bindTap: 'bindKakaoTalk',
        status: false
      },
      {
        name: '警右',
        bindTap: 'bindJinyou',
        status: false
      },
      {
        name: 'Messenger',
        bindTap: 'bindMessenger',
        status: false
      },
      {
        name: '其他应用',
        bindTap: 'bindOthers',
        status: false
      },
    ]
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
    // let str = 'ad010101010100010100010101010101010000'
    let str = 3
    console.log("this.judgePackage('01')=>", this.convertToBinaryAndShift(str))
  },


  convertToBinaryAndShift(input: any) {
    // 转换为二进制
    let binary = (input >>> 0).toString(2);
    return {
      binary: binary
    };
  },
  allStop() {
    let datas = {
      VPSettingCall: 'stop',
      VPSettingSMS: 'stop',
      VPSettingWechat: 'stop',
      VPSettingQQ: 'stop',
      VPSettingSina: 'stop',
      VPSettingFaceBook: 'stop',
      VPSettingTwitter: 'stop',
      VPSettingFlickr: 'stop',
      VPSettingLinkedln: 'stop',
      VPSettingwhatsapp: 'stop',
      VPSettingLine: 'stop',
      VPSettingInstagram: 'stop',
      VPSettingSnapchat: 'stop',
      VPSettingSkype: 'stop',
      VPSettingGMail: 'stop',
      VPSettingDingTalk: 'stop',
      VPSettingWeChatWork: 'stop',
      VPSettingOtherTikTok: 'stop',
      VPSettingOtherTelegram: 'stop',
      VPSettingOtherConnected2: 'stop',
      VPSettingKakaoTalk: 'stop',
      VPSettingJingYou: 'stop',
      VPSettingMessenger: 'stop',
      VPSettingOthers:'stop'
    }
    veepooFeature.veepooSendANCSSwitchControlDataManager(datas)
  },
  allStart() {
    let datas = {
      VPSettingCall: 'start',
      VPSettingSMS: 'start',
      VPSettingWechat: 'start',
      VPSettingQQ: 'start',
      VPSettingSina: 'start',
      VPSettingFaceBook: 'start',
      VPSettingTwitter: 'start',
      VPSettingFlickr: 'start',
      VPSettingLinkedln: 'start',
      VPSettingwhatsapp: 'start',
      VPSettingLine: 'start',
      VPSettingInstagram: 'start',
      VPSettingSnapchat: 'start',
      VPSettingSkype: 'start',
      VPSettingGMail: 'start',
      VPSettingDingTalk: 'start',
      VPSettingWeChatWork: 'start',
      VPSettingOtherTikTok: 'start',
      VPSettingOtherTelegram: 'start',
      VPSettingOtherConnected2: 'start',
      VPSettingKakaoTalk: 'start',
      VPSettingJingYou: 'start',
      VPSettingMessenger: 'start',
      VPSettingOthers: 'start'
    }
    veepooFeature.veepooSendANCSSwitchControlDataManager(datas)
  },

  totalBindTap(e: any) {
    console.log("e=>", e)
    let deviceSwitch = e.detail.value;
    let name = e.target.dataset.name
    let toastList = this.data.toastList;
    toastList.forEach((item, index: any) => {
      if (item.name == name) {
        item.status = deviceSwitch
      }
    })
    this.setData({
      toastList
    })
    this.sendChangeValue()
  },

  getSwitchStatus(bool: boolean) {
    if (bool) {
      return 'start'
    } else {
      return 'stop'
    }
  },
  sendChangeValue() {
    let data = this.data.datas;
    let toastList = this.data.toastList;
    data.VPSettingCall = this.getSwitchStatus(toastList[0].status)
    data.VPSettingSMS = this.getSwitchStatus(toastList[1].status)
    data.VPSettingWechat = this.getSwitchStatus(toastList[2].status)
    data.VPSettingQQ = this.getSwitchStatus(toastList[3].status)
    data.VPSettingSina = this.getSwitchStatus(toastList[4].status)
    data.VPSettingFaceBook = this.getSwitchStatus(toastList[5].status)
    data.VPSettingTwitter = this.getSwitchStatus(toastList[6].status)
    data.VPSettingFlickr = this.getSwitchStatus(toastList[7].status)
    data.VPSettingLinkedln = this.getSwitchStatus(toastList[8].status)
    data.VPSettingwhatsapp = this.getSwitchStatus(toastList[9].status)
    data.VPSettingLine = this.getSwitchStatus(toastList[10].status)
    data.VPSettingInstagram = this.getSwitchStatus(toastList[11].status)
    data.VPSettingSnapchat = this.getSwitchStatus(toastList[12].status)
    data.VPSettingSkype = this.getSwitchStatus(toastList[13].status)
    data.VPSettingGMail = this.getSwitchStatus(toastList[14].status)
    data.VPSettingDingTalk = this.getSwitchStatus(toastList[15].status)
    data.VPSettingWeChatWork = this.getSwitchStatus(toastList[16].status)
    data.VPSettingOtherTikTok = this.getSwitchStatus(toastList[17].status)
    data.VPSettingOtherTelegram = this.getSwitchStatus(toastList[18].status)
    data.VPSettingOtherConnected2 = this.getSwitchStatus(toastList[19].status)
    data.VPSettingKakaoTalk = this.getSwitchStatus(toastList[20].status)
    data.VPSettingJingYou = this.getSwitchStatus(toastList[21].status)
    data.VPSettingMessenger = this.getSwitchStatus(toastList[22].status)
    data.VPSettingOthers = this.getSwitchStatus(toastList[23].status)
    console.log("data=====>", data)
    veepooFeature.veepooSendANCSSwitchControlDataManager(data)
  },


  bindLaidian(e: any) {
    this.totalBindTap(e);

  },

  bindDuanxing(e: any) {
    this.totalBindTap(e);

  },
  bindWechat(e: any) {
    this.totalBindTap(e);

  },
  bindQQ(e: any) {
    this.totalBindTap(e);

  },
  bindSina(e: any) {
    this.totalBindTap(e);

  },
  bindFaceBook(e: any) {
    this.totalBindTap(e);

  },
  bindTwitter(e: any) {
    this.totalBindTap(e);

  },
  bindLinkedln(e: any) {
    this.totalBindTap(e);

  },
  bindWhatsapp(e: any) {
    this.totalBindTap(e);

  },
  bindLine(e: any) {
    this.totalBindTap(e);

  },
  bindInstagram(e: any) {
    this.totalBindTap(e);

  },
  bindSnapchat(e: any) {
    this.totalBindTap(e);

  },
  bindSkype(e: any) {
    this.totalBindTap(e);

  },
  bindGmail(e: any) {
    this.totalBindTap(e);

  },
  bindDingDing(e: any) {
    this.totalBindTap(e);

  },
  bindQiyeweixin(e: any) {
    this.totalBindTap(e);

  },
  bindTiktok(e: any) {
    this.totalBindTap(e);

  },
  bindTelegram(e: any) {
    this.totalBindTap(e);

  },
  bindConnected2(e: any) {
    this.totalBindTap(e);

  },
  bindKakaoTalk(e: any) {
    this.totalBindTap(e);

  },
  bindJinyou(e: any) {
    this.totalBindTap(e);

  },
  bindMessenger(e: any) {
    this.totalBindTap(e);
  },
  bindOthers(e: any) {
    this.totalBindTap(e);
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("监听蓝牙回调=>", e);
    })
  },
})