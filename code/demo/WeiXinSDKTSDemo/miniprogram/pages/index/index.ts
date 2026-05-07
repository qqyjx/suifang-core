
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'

import { veepooJLAuthenticationManager, veepooJLDisconnectDevice } from "../../jieli_sdk/index"
import { BleDataHandler } from '../../jieli_sdk/lib/ble-data-handler';
import { veepooJLBle } from "../../jieli_sdk/bleInit"
import { ENV } from "../../services/env"
import { dispatchBleData } from "../../services/bleDispatcher"
import { dataStorage } from "../../services/dataStorage"
// const vpJLBle = new veepooJLBle();
//打印设置

let imagePath = 'file:///data/storage/el2/base/haps/entry/files/custom_dial_images/2025430_114654.jpg';

let path = imagePath.split(":")[1]

console.log("path=>", path.substring(2));

// 获取应用实例
const app = getApp<IAppOption>()
// 血液 血糖bug修复
Component({
  data: {
    bleList: [],
    device: {},
    info: {},
    connected: false,
    isTestBuild: ENV.IS_TEST_BUILD,
    buildTag: ENV.BUILD_TAG,
    patientNo: '', // 5.06-v9: 当前患者门诊号 (storage.patientNo 同步)
    listDate: [
      {
        name: '📊 数据管理',
        path: '/pages/dataManagement/index'
      },
      {
        name: '切换服务',
        path: 'switchServices'
      },
      {
        name: '蓝牙重连',
        path: 'Reconnect'
      },
      {
        name: '波形',
        path: '/pages/waveform/index'
      },
      {
        name: '断开连接',
        path: 'DisconnectBluetooth'
      },
      {
        name: '单位设置',
        path: '/pages/unitSetting/index'
      },
      {
        name: '天气',
        path: '/pages/weatherForecast/index'
      },
      {
        name: '个人信息',
        path: '/pages/personalInfo/index'
      },
      {
        name: '日常数据',
        path: '/pages/readDailyData/index'
      },
      {
        name: '睡眠',
        path: '/pages/sleep/index'
      },
      {
        name: '计步',
        path: '/pages/step/index'
      },
      {
        name: '体温手动',
        path: '/pages/bodyTemperature/index'
      },
      {
        name: 'ECG测量',
        path: '/pages/ecgTest/index'
      },
      {
        name: 'PTT测量',
        path: '/pages/pttTest/index'
      },
      {
        name: 'ECG读取',
        path: '/pages/ecgRead/index'
      },
      {
        name: '身体成分',
        path: '/pages/bodyMeasurement/index'
      },
      {
        name: '体温自动',
        path: '/pages/bodyTemperatureAuto/index'
      },
      {
        name: '联系人',
        path: '/pages/contactPerson/index'
      },
      {
        name: 'SOS',
        path: '/pages/sos/index'
      },
      {
        name: '闹钟',
        path: '/pages/alarmClock/index'
      },
      {
        name: '运动模式',
        path: '/pages/movementPattern/index'
      },
      {
        name: '表盘相关',
        path: '/pages/dial/index'
      },
      {
        name: '查找手机',
        path: '/pages/lookPhone/index'
      },
      {
        name: '血压',
        path: '/pages/universalBlood/index'
      },
      {
        name: '屏幕设置',
        path: '/pages/screenSetup/index'
      },
      {
        name: '心率报警',
        path: '/pages/heartRateAlarm/index'
      },
      {
        name: '血液成分',
        path: '/pages/bloodComponent/index'
      },
      {
        name: '血糖测量',
        path: '/pages/bloodGlucose/index'
      },
      {
        name: 'ota',
        path: '/pages/ota/index'
      },
      // {
      //   name: 'ota原生版',
      //   path: '/pages/otaNavite/index'
      // },
      {
        name: '久坐提醒',
        path: '/pages/sedentaryToast/index'
      },
      {
        name: '拍照',
        path: '/pages/takeAPicture/index'
      },
      {
        name: '抬手亮屏',
        path: '/pages/brightScreen/index'
      },
      {
        name: 'ANCS开关',
        path: '/pages/ANCSToast/index'
      },
      {
        name: '健康提醒',
        path: '/pages/healthToast/index'
      },
      {
        name: '血氧自动',
        path: '/pages/bloodOxygen/index'
      },
      {
        name: '血氧手动',
        path: '/pages/bloodOxygen2/index'
      },
      {
        name: '女性经期',
        path: '/pages/female/index'
      },
      {
        name: '恢复出厂',
        path: 'resettingTheDevice'
      },
      {
        name: '复位',
        path: 'reset'
      },
      {
        name: '开关设置',
        path: '/pages/switchSetup/index'
      },
      {
        name: 'android编码',
        path: '/pages/androidCode/index'
      },
      {
        name: 'UI风格',
        path: '/pages/uiStyle/index'
      },
      {
        name: '同步时间',
        path: '/pages/syncTime/index'
      },
      {
        name: '网络表盘',
        path: '/pages/networkDial/index'
      },
      {
        name: '心率测量',
        path: '/pages/heartRateTest/index'
      },
      {
        name: '语言切换',
        path: '/pages/languagePage/index'
      },
      {
        name: '读取手动测量',
        path: '/pages/manualMeasurement/index'
      },
      {
        name: '肤色设置',
        path: '/pages/skinColorSetting/index'
      },
      {
        name: '微体检',
        path: '/pages/microCheck/index'
      },
      {
        name: 'B3自动测量',
        path: '/pages/b3AutoTestFeature/b3AutoTestFeature'
      },
      {
        name: 'JH58',
        path: '/pages/JH58/index'
      },
      {
        name: 'ZT163常灭屏',
        path: '/pages/ZT163ScreenKillFunction/index'
      },
      {
        name: '4G服务',
        path: '/pages/4GService/Index'
      },
    ],
    valData: {
      heartRate: 'start',
      bloodPressure: 'stop',
    }
  },
  methods: {

    /**
     * 5.06-v9: 患者门诊号输入/切换. 没填或想换人就点首页"输入门诊号 / 切换患者"按钮.
     *   - 弹 wx.showModal 带 editable 输入框, 输入后存 storage.patientNo + setData
     *   - 后续 dataStorage.enqueueForBatch / flushPending 会自动从 storage 读最新值带上
     *   - 切换患者只需重新输入新门诊号, 无任何系统级动作 (不断蓝牙, 不重连)
     *
     * 客户实际场景: 妈妈一个微信扫码进小程序 → 输入孩子A 100234 → 测完点切换 → 输入孩子B 100235.
     */
    onPatientNoTap() {
      const self = this as any;
      const current: string = wx.getStorageSync('patientNo') || '';
      wx.showModal({
        title: current ? '切换患者门诊号' : '请输入患者门诊号',
        content: current
          ? `当前门诊号: ${current}\n切换后, 后续采集的数据会归属到新门诊号.`
          : '采集前请输入患者的门诊号. 后续每条体征数据会带此门诊号入库, 用于区分不同患者.',
        editable: true,
        placeholderText: '例如: 100234',
        confirmText: '确定',
        cancelText: '取消',
        success: (m: any) => {
          if (!m.confirm) return;
          const raw = (m.content || '').trim();
          if (!raw) {
            wx.showToast({ title: '门诊号不能为空', icon: 'none', duration: 1500 });
            return;
          }
          if (raw === current) {
            wx.showToast({ title: '门诊号未变', icon: 'none', duration: 1200 });
            return;
          }
          wx.setStorageSync('patientNo', raw);
          self.setData({ patientNo: raw });
          wx.showToast({ title: `已切换至 ${raw}`, icon: 'success', duration: 1500 });
        },
      });
    },

    /**
     * 5.06-v8: 用户主动点击"重新连接"按钮 (首页状态条).
     * 清掉 userDisconnected 标志 (允许重连), 然后调 BleHub.requestReconnect 走集中式重连.
     * BleHub 内部指数退避 1s/2s/4s 三次, 互斥锁保证不重复发起.
     */
    onReconnectTap() {
      const bleInfo: any = wx.getStorageSync('bleInfo');
      if (!bleInfo || !bleInfo.deviceId) {
        wx.showModal({
          title: '没有可重连的设备',
          content: '尚未绑定过手表, 请先到 "设备扫描" 页选一个设备连接.',
          showCancel: false,
        });
        return;
      }
      wx.removeStorageSync('userDisconnected');
      wx.showToast({ title: '正在重连…', icon: 'none', duration: 2000 });
      try {
        require('../../services/bleHub').bleHub.requestReconnect('user-tap-reconnect');
      } catch (e) {
        console.warn('[index] requestReconnect 抛错', e);
      }
    },

    packRgb(r: any, g: any, b: any) {

      // 构造高位字节
      console.log("(r << 3) & 0xF8)=>", (r << 3) & 0xF8)
      console.log("((g >> 3) & 0x07)=>", ((g >> 3) & 0x07))
      let big = ((r << 3) & 0xF8) | ((g >> 3) & 0x07); // 注意：在JavaScript中我们需要左移r以腾出空间

      // 构造低位字节
      let little = ((g << 5) & 0xe0) | (b & 0x1F); // 注意：在JavaScript中我们左移g 5位以腾出空间

      return { big, little };
    },
    getF003() {
      let device: any = wx.getStorageSync('bleDate')
      wx.getBLEDeviceServices({
        // 这里的 deviceId 需要已经通过 wx.createBLEConnection 与对应设备建立连接
        deviceId: device.deviceId,
        success(res) {
          console.log('device services:', res.services)
          let date = res.services;
          for (let i = 0; i < date.length; i++) {
            if (date[i].uuid == 'F0020001-0451-4000-B000-000000000000') {
              wx.getBLEDeviceCharacteristics({
                deviceId: device.deviceId,
                serviceId: date[i].uuid,
                success(res) {
                  console.log('device getBLEDeviceCharacteristics:', res.characteristics);
                  res.characteristics.forEach((item: any, index: number) => {
                    if (item.properties.notify) {
                      console.log("item==>", item)
                      wx.notifyBLECharacteristicValueChange({
                        state: true, // 启用 notify 功能
                        deviceId: device.deviceId,
                        serviceId: date[i].uuid,
                        characteristicId: item.uuid,
                        success(res) {
                          console.log("监听ecg特征成功=>", res)
                          wx.onBLECharacteristicValueChange(function (res) {
                            console.log("res=>", res)
                          })
                        },
                        fail(err) {
                          console.log("监听ecg特征失败err=>", err)
                        }
                      })
                    }
                  })

                }, fail(err) {
                  console.log('err=>', err)
                }
              })
            }
          }
        }, fail(err) {
          console.log(err)
        }
      })
    },


    blePwd() {

      console.log("蓝牙秘钥核准")
      this.BlePasswordCheckManager();

    },

    onShow() {
      let self = this;
      // 5.06-v9: 同步 storage.patientNo 到 data, 防止跳转回首页时角标显示旧值
      self.setData({ patientNo: wx.getStorageSync('patientNo') || '' });

      // let blePackage = {"deviceId":"FA:4E:30:9C:E6:B0","rssi":-38,"connectable":true,"data":{"0":2,"1":1,"2":6,"3":9,"4":255,"5":248,"6":248,"7":76,"8":197,"9":217,"10":146,"11":3,"12":248,"13":3,"14":3,"15":231,"16":254,"17":5,"18":9,"19":86,"20":50,"21":55,"22":90},"deviceName":"V27Z"}
      // 初始化蓝牙适配器

      let blePackage = { "deviceId": "F0:87:99:D6:F7:2D", "rssi": -53, "connectable": true, "data": { "0": 2, "1": 1, "2": 6, "3": 3, "4": 3, "5": 231, "6": 254, "7": 3, "8": 25, "9": 65, "10": 3, "11": 9, "12": 255, "13": 248, "14": 248, "15": 46, "16": 28, "17": 105, "18": 64, "19": 211, "20": 20, "21": 6, "22": 9, "23": 70, "24": 49, "25": 48, "26": 48, "27": 0 }, "deviceName": "F100" }


      const valuesArray = Object.values(blePackage.data);
      console.log("valuesArray=>", valuesArray)
      let hexValue = valuesArray.map(value => value.toString(16).toUpperCase().padStart(2, '0'));
      console.log('hexValue==>', hexValue)
      let hexMac = hexValue.splice(15, 6).reverse();
      let max = '';
      for (let i = 0; i < hexMac.length; i++) {
        if (i == hexMac.length - 1) {
          max = max + hexMac[i]
        } else {
          max = max + hexMac[i] + ':'
        }
      }
      console.log("mac=>", max);

      let dataLength = [2, 1, 6, 3, 3, 231, 254, 3, 25, 65, 3, 9, 255, 248, 248, 46, 28, 105, 64, 211, 20, 6, 9, 70, 49, 48, 48, 0].length
      let tbyte = [2, 1, 6, 3, 3, 231, 254, 3, 25, 65, 3, 9, 255, 248, 248, 46, 28, 105, 64, 211, 20, 6, 9, 70, 49, 48, 48, 0]

      let hex = ["02", "01", "06", "03", "03", "E7", "FE", "03", "19", "41", "03", "09", "FF", "F8", "F8", "2E", "1C", "69", "40", "D3", "14", "06", "09", "46", "31", "30", "30", "00"]


      for (let i = 0; i < hex.length; i++) {
        if (hex[i] + hex[i + 1] == 'F8F8') {
          console.log('index====>', i + 2)
        }

      }

      if (dataLength >= 8) {
        let endIndex: number = 7;
        if (tbyte[0] === 0xF8 && tbyte[1] === 0xF9) {
          endIndex += 4;
        }


      }

      veepooBle.veepooWeiXinSDKStopSearchBleManager(function (e: any) {
        console.log("停止蓝牙搜索=>", e)
      })
      this.getConnectedBleDevice();

      wx.onBLEConnectionStateChange((res: any) => {
        // 该方法回调中可以用于处理连接意外断开等异常情况.
        // 注意: iOS 系统设置里 "断开" 不一定触发本回调 (那是 iOS 系统层断, 小程序的 BLE session
        //       仍然活着). 真正会触发的是: 表关机/电池没电、出蓝牙范围、wx.closeBLEConnection.
        console.log(`[index] BLE state -> device=${res.deviceId} connected=${res.connected}`)
        if (!res.connected) {
          // 真断了: 清 storage 快照 + UI 字段, 防止 "断了但页面还显示数据" 的错觉.
          wx.setStorageSync('VPDevice', null)
          self.setData({ device: {}, connected: false, info: {} })
        }
      })
      const items = Array.from({
        length: 40
      }, () => 0)
      console.log("items=>", items)


      let data = {
        status: true
      }
      veepooBle.veepooWeiXinSDKRawDataShowStatus(data);

      let str = "2025-09-03 17:48:00";
      const isoStr = str.replace(' ', 'T');
      const date = new Date(isoStr);
      console.log('时间戳==》', date.getTime());

    },

    getNextDays(startDate: any, count: number) {
      let days = [];
      for (let i = 0; i < count; i++) {
        let day = new Date(startDate);
        day.setDate(day.getDate() - i);
        days.push(day.toISOString().split('T')[0]); // 转换为 YYYY-MM-DD 格式  
      }
      return days;
    },

    uint8ArrayToHex(uInt8Array: any) {
      return uInt8Array.map((byte: any) => byte.toString(16).padStart(2, '0'));
    },
    updateProgress(currentValue: any, maxValue: any) {
      const progressWidth = (maxValue - currentValue) / maxValue * 100;
      return progressWidth
    },

    setJLVerify() {
      let self = this;
      // 初始化，接受杰里数据
      BleDataHandler.init()
      let device = wx.getStorageSync('bleInfo')
      // 杰里设备认证
      setTimeout(() => {
        // 杰里设备认证
        veepooJLAuthenticationManager(device, (res: any) => {
          console.log("杰理认证状态==>", res)
        })
      }, 2000);
    },
    // 获取背景信息
    getBackgroundInfo() {
      let data = {
        type: 1
      }
      veepooFeature.veepooSendReadCustomBackgroundDailManager(data);
    },

    // 断开连接
    closeBluetoothAdapterManager() {
      let self = this;
      let device = wx.getStorageSync('bleInfo');

      // 1. 真断 BLE 物理通道, 否则手表协议层认为还连着 -> 不再广播 -> 下次扫不到
      if (device && device.deviceId) {
        try {
          wx.closeBLEConnection({
            deviceId: device.deviceId,
            success: (r) => console.log('[断开] closeBLEConnection ok', r),
            fail: (e) => console.warn('[断开] closeBLEConnection fail', e),
          });
        } catch (e) { console.warn('[断开] closeBLEConnection 异常', e); }
      }

      // 2. 杰理 SDK 也清一下连接状态/认证缓存
      try { veepooJLDisconnectDevice(device) } catch (e) { console.warn('[断开] jieli disconnect 异常', e); }

      // 3. 清 deviceId 缓存, 让下次重连用 MAC 重新走 register
      dataStorage.resetDeviceIdCache();

      // 4. 区分 "用户主动按断开" vs "系统挂起断开":
      //    - 主动断开: 设 userDisconnected flag, app.ts onShow 跳过自动重连,
      //      避免 "我都按断开了它还自动连回来" 的反直觉.
      //    - 挂起断开: bleInfo 在, 没 flag, onShow 自动重连 (没连好就重连).
      //    bleInfo 不清 null 避免 7 处 bleInfo.deviceId 引发 TypeError 链 (v3 教训).
      //    flag 在 bleConnection 连接成功时清 (用户重新选设备 = 重新允许自动重连).
      wx.setStorageSync('userDisconnected', true);

      // 5. UI 状态同步
      self.setData({
        device: {},
        isConnected: false,
        connected: false,
        info: {},
      })

      wx.showToast({ title: '已断开，可重新搜索', icon: 'none', duration: 1500 });
    },
    // 获取已连接的蓝牙设备 + 校正 BLE 真实状态
    //
    // 两个易踩的坑:
    //   1) iOS 系统蓝牙的 "断开" 不会触发 wx.onBLEConnectionStateChange,
    //      也不会让 wx 这边的 BLE session 失效 (小程序 BLE 通道独立).
    //   2) storage 的 VPDevice 是上一次连接时 BleHub 写入的快照, 即使
    //      BLE 真断了也会留着 -> 直接信任快照会让 UI 显示假数据.
    //
    // 解法: 进首页时主动探测 BLE 真实状态 (wx.getBLEDeviceServices),
    //   - 真连着  -> 显示快照 + 走 forceEnableNotify + 密钥核准
    //   - 真断了  -> 清 storage VPDevice + 清 UI 字段, 让用户明确看到 "未连接"
    //   - 用户主动断开 (userDisconnected flag) -> 同上, 立即清
    getConnectedBleDevice() {
      let self = this;
      const bleInfo: any = wx.getStorageSync('bleInfo');
      const userDisconnected = wx.getStorageSync('userDisconnected');

      // 主动断开过 / 没 bleInfo: 立即清, 不显示老快照
      if (userDisconnected || !bleInfo || !bleInfo.deviceId) {
        wx.removeStorageSync('VPDevice');
        self.setData({ device: {}, connected: false, info: {} });
        return;
      }

      // 有 bleInfo + 没用户主动断开. 先用 storage 快照让 UI 立刻有数据,
      // 再异步探测 BLE 真实状态校正 (探测失败 -> 清; 成功 -> 走原流程).
      const snap: any = wx.getStorageSync('VPDevice');
      if (snap) self.setData({ device: snap, connected: true });

      wx.getBLEDeviceServices({
        deviceId: bleInfo.deviceId,
        success(_res: any) {
          // BLE 通道真连着, 走原流程
          console.log('[index] BLE 真实状态: 已连接, 走 forceEnableNotify + 密钥核准');
          self.setData({ info: { deviceId: bleInfo.deviceId, name: (snap && snap.name) || bleInfo.name }, connected: true });
          self.notifyMonitorValueChange();
          try { require('../../services/bleHub').bleHub.forceEnableNotify(bleInfo.deviceId); }
          catch (err) { console.warn('[index] forceEnableNotify 触发失败', err); }
          setTimeout(() => self.BlePasswordCheckManager(), 800);
        },
        fail(err: any) {
          // BLE 实际断了 (典型 errCode 10006 = no connection / device disconnect).
          // 这是 iOS 系统设置里 "断开" 后唯一靠谱的判定路径.
          console.log('[index] BLE 真实状态: 未连接, 清空 UI', err && err.errMsg);
          wx.removeStorageSync('VPDevice');
          self.setData({ device: {}, connected: false, info: {} });
        }
      });
    },
    skipDeviceGet() {
      console.log("a")
      wx.navigateTo({
        url: '/pages/bleConnection/index',
      })
    },
    // 跳转相关页面
    skipPages(e: any) {
      let path = e.currentTarget.dataset.path;
      let self = this;
      console.log(path)
      if (path == 'DisconnectBluetooth') {
        veepooFeature.veepooSendDisconnectBluetoothDataManager()
        return
      }
      if (path == 'resettingTheDevice') {
        veepooFeature.veepooSendResettingTheDeviceDataManager()
        return
      }
      if (path == 'reset') {
        veepooFeature.veepooSendResetDataManager()
        return
      }

      if (path == 'switchServices') {

        // 获取存储的蓝牙信息
        const device = wx.getStorageSync('bleInfo');

        // 切换服务
        veepooBle.veepooWeiXinSDKHandoverServiceManager({ deviceId: device.deviceId }, (res: any) => {
          console.log("服务切换res=>", res)
        });

        return
      }

      if (path == "Reconnect") {
        let item = wx.getStorageSync('bleInfo');
        veepooBle.veepooWeiXinSDKBleReconnectDeviceManager(item, function (result: any) {
          console.log('蓝牙重连result=>', result);
          // 获取当前服务，订阅监听
          self.notifyMonitorValueChange();
          // 蓝牙密码核准
          veepooFeature.veepooBlePasswordCheckManager();

        })
        return
      }


      // switchServices
      // Reconnect

      wx.navigateTo({
        url: path,
      })
    },
    // 密钥核验  无参数
    BlePasswordCheckManager() {
      let self = this;
      let VPDevice = wx.getStorageSync('VPDevice');

      console.log("VPDevice==>", VPDevice)
      if (VPDevice) {
        self.setData({
          device: VPDevice
        })
      } else {
        veepooFeature.veepooBlePasswordCheckManager();
      }

      console.log("读取电量")
      this.ElectricQuantityManager();
      console.log("读取步数")
      this.StepCalorieDistanceManager();
      // bleDate 是历史拼写错误, 实际写入 storage 的 key 是 bleInfo
      const bleInfo: any = wx.getStorageSync('bleInfo')
      if (!bleInfo || !bleInfo.deviceId) return
      wx.setBLEMTU({
        deviceId: bleInfo.deviceId,
        mtu: 247,
        success: res => console.log("setBLEMTU=>", res),
        fail: () => {
          wx.getBLEMTU({
            deviceId: bleInfo.deviceId,
            success: res => console.log("getBLEMTU=>", res),
          })
        }
      })
    },
    // 电量读取
    ElectricQuantityManager() {
      veepooFeature.veepooReadElectricQuantityManager();
    },
    // 读取步数，距离卡路里
    /*
    参数：day:0； 0 今天 1 昨天 2 前天 
     */
    StepCalorieDistanceManager() {
      let data = {
        day: 0
      }
      veepooFeature.veepooReadStepCalorieDistanceManager(data)
    },
    // 监听订阅 notifyMonitorValueChange
    notifyMonitorValueChange() {
      let self = this;
      veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
        self.bleDataParses(e)
      })
    },
    // 监听蓝牙返回数据解析
    bleDataParses(value: any) {
      // 防御性: SDK 偶尔会传 undefined 给 listeners (BleHub 入口已过滤大部分,
      // 但 page 自己的 cb 可能还有别的入口收到), 不加 guard 会让 value.type 抛
      // TypeError: undefined is not an object (evaluating 'e.type'), 污染 vConsole.
      if (!value || typeof value !== 'object' || typeof value.type === 'undefined') return;
      let self = this;
      let device: any = this.data.device;
      console.log("蓝牙监听返回= 这个是index页面 >", value)
      // 校验
      if (value.type == 1) {
        device.VPDeviceVersion = value.content.VPDeviceVersion;
        device.VPDeviceMAC = value.content.VPDeviceMAC;
        wx.setStorageSync('VPDevice', device)
        self.setData({
          device
        })
      } else if (value.type == 2) {
        device.VPDeviceElectricPercent = value.content.VPDeviceElectricPercent;
        self.setData({
          device
        })
      } else if (value.type == 9) {
        device.step = value.content.step;
        device.calorie = value.content.calorie;
        device.distance = value.content.distance;
        self.setData({
          device
        })
        // 存储自定义背景类型，方便获取屏幕信息
      } else if (value.type == 46) {
        let type = value.content.customDialType
        wx.setStorageSync('customType', type)
      }
      // 全局兜底：手表本机自测数据（血压/血氧/体温/血糖/血液成分/身体成分等）
      // 患者不会进对应功能 page，需要在默认停留页落库 + 上传六元数据库
      dispatchBleData(value)
    }
  },
})
