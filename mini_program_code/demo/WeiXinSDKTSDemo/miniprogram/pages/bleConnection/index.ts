// pages/bleConnection/index.ts
import { veepooBle, veepooFeature } from '../../miniprogram_dist/index'
import { dataStorage } from '../../services/dataStorage'
import { ENV } from '../../services/env'
import { dispatchBleData } from '../../services/bleDispatcher'

// 5.06-v8: 扫描超时 30s -> 60s, 第一轮空再自动续扫一轮.
// 客户反馈连接成功率不高, 60s 给 iOS BLE 缓存释放 + 用户走近手表足够时间.
const SCAN_TIMEOUT_MS = 60000
const MAX_SCAN_ROUNDS = 2
const HANDSHAKE_TIMEOUT_MS = 12000








Page({

  /**
   * 页面的初始数据
   */
  data: {
    bleList: [],
    isIOS: false,
    statusText: '扫描中…',
    isTestBuild: ENV.IS_TEST_BUILD,
    scanRound: 0,        // 5.06-v8: 当前扫描轮次, 显示用
    scanning: false,     // 5.06-v8: 扫描中 → wxml 可显示 loading + "重新搜索" 隐藏
  },

  scanTimer: 0 as any,
  scanSeenCount: 0,
  scanMatchedCount: 0,
  // 5.06-v8: 累积的设备列表 (跨多轮扫描). 提到 instance 字段避免被闭包重置.
  bleArr: [] as any[],
  // 5.06-v8: stale "上次连接" 项只在第一轮入列, 续扫不重复 push.
  staleAdded: false,
  // 5.06-v8: 早期权限/物理层根因检测定时器. 5s 内没扫到任何设备 (含非目标) -> 弹引导.
  earlyDiagTimer: 0 as any,
  // 已弹过早期诊断 modal -> 不重复打扰
  earlyDiagShown: false,

  /**
   * 生命周期函数--监听页面加载
   */
  onLoad() {
    let self = this;
    try {
      const info = wx.getDeviceInfo ? wx.getDeviceInfo() : wx.getSystemInfoSync()
      if ((info as any).platform === 'ios') self.setData({ isIOS: true })
    } catch (e) {
      console.warn('[bleConnection] getDeviceInfo failed, fallback skipped:', e)
    }

    // 修 "断开后再连搜不到 S101" 的关键: 完整重置 BLE 通道.
    //
    // 用户路径:
    //   首页连上 -> 看到 4 字段 -> 点 "断开连接" 或 iOS 系统断开
    //   -> 进 "设备扫描" 页 -> 一直 "正在搜索..." 搜不到 S101
    //
    // 根因: closeBLEConnection 只切了 wx 应用层 BLE session, iOS CoreBluetooth
    // 系统层的 paired 状态可能仍持有, 表停在 paired-disconnected 不广播.
    // 必须 closeBluetoothAdapter (整个 BLE 适配器关) + openBluetoothAdapter
    // (重新打开) 让 wx 端忘记旧连接, iOS 释放 pair, 表重新进入 advertising.
    const startScan = () => {
      // 5.06-v8: attach diagnostics 只做一次 (一次性 setup), 后续续扫只调 startScanRound.
      // BLEConnectionStateChange 已由 BleHub 全局接管, 这里不再注册避免覆盖.
      if (ENV.IS_TEST_BUILD) self.attachRawBleDiagnostics();
      self.startScanRound();
    };

    const stale: any = wx.getStorageSync('bleInfo');
    const closeOldConnFirst = (cb: () => void) => {
      if (stale && stale.deviceId) {
        wx.closeBLEConnection({
          deviceId: stale.deviceId,
          complete: (r: any) => {
            console.log('[bleConnection] 进扫描页前 close 旧 BLE', r && r.errMsg);
            cb();
          },
        });
      } else {
        cb();
      }
    };

    closeOldConnFirst(() => {
      // 重置 BLE adapter (close 整个适配器再 open).
      // 这一步是修扫不到的杀手锏: iOS 上仅 closeBLEConnection 不够, 系统层的
      // paired 状态会让表停在 paired-disconnected. closeBluetoothAdapter
      // 等于跟 iOS 说 "我不要这个 BLE session 了", 触发系统释放 pair.
      wx.closeBluetoothAdapter({
        complete: () => {
          setTimeout(() => {
            wx.openBluetoothAdapter({
              complete: (r: any) => {
                console.log('[bleConnection] BLE adapter 重置完成, 启动扫描', r && r.errMsg);
                startScan();
              },
            });
          }, 300);
        },
      });
    });
  },

  /**
   * 5.06-v8: 用户手动重新搜索. wxml 上 "重新搜索" 按钮触发.
   * 复用 onLoad 的完整重置流程: 关闭旧连接 + 重置 BLE adapter + 启动新扫描轮次.
   */
  onRescanTap() {
    if (this.data.scanning) return;
    if (this.scanTimer) { clearTimeout(this.scanTimer); this.scanTimer = 0; }
    if (this.earlyDiagTimer) { clearTimeout(this.earlyDiagTimer); this.earlyDiagTimer = 0; }
    // 不清 bleArr (保留累积发现, 用户可能想看上轮的设备)
    // 但清扫描计数器 + scanRound + earlyDiagShown, 让续扫逻辑+早期诊断从头来
    this.setData({ scanRound: 0, statusText: '正在重新搜索…' });
    this.staleAdded = false; // stale 重 push 一次, 防被剔除后想重选
    this.earlyDiagShown = false;
    this.startScanRound();
  },

  // 体验版诊断：直接挂微信原生 onBluetoothDeviceFound + adapterState
  // 区分 "SDK 回调没触发" vs "微信原生层就没结果"（多半是被其他 App 占用 / iOS 权限拒绝）
  attachRawBleDiagnostics() {
    try {
      wx.onBluetoothAdapterStateChange((res: any) => {
        console.log('[BLE raw adapter]', JSON.stringify(res))
      })
      wx.onBluetoothDeviceFound((res: any) => {
        const ds = res && res.devices || []
        ds.forEach((d: any) => {
          console.log('[BLE raw found]', d.name || d.localName || '(无名)',
            'RSSI=', d.RSSI, 'id=', d.deviceId)
        })
      })
    } catch (e) {
      console.warn('[bleConnection] raw diagnostics attach failed:', e)
    }
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
  onHide() {
    this.StopSearchBleManager()
    if (this.scanTimer) { clearTimeout(this.scanTimer); this.scanTimer = 0 }
    if (this.earlyDiagTimer) { clearTimeout(this.earlyDiagTimer); this.earlyDiagTimer = 0 }
  },

  onUnload() {
    if (this.scanTimer) { clearTimeout(this.scanTimer); this.scanTimer = 0 }
    if (this.earlyDiagTimer) { clearTimeout(this.earlyDiagTimer); this.earlyDiagTimer = 0 }
  },

  /**
   * 5.06-v8: 启动一轮扫描. 60s 超时后:
   *   - 扫到设备 -> 停止, UI 显示列表
   *   - 一个都没扫到, 且 scanRound<MAX_SCAN_ROUNDS -> 自动续扫一轮 (静默不打扰)
   *   - 续扫后仍 0 个 -> iOS 弹"系统蓝牙忽略此设备"引导; 安卓弹通用排查清单
   *
   * bleArr / staleAdded 提到 instance 字段, 续扫不重置已发现的设备.
   * Veepoo SDK 的 callback 内部对 deviceId 去重, 续扫不会产生重复行.
   */
  startScanRound() {
    let self = this;
    const round = (self.data.scanRound || 0) + 1;
    self.setData({ scanRound: round, scanning: true });
    self.scanSeenCount = 0;
    self.scanMatchedCount = 0;

    // ===== 第一轮: 把上次连接的设备 (bleInfo) 加到列表第一项 =====
    //
    // iOS 平台限制: 已与系统配对的 BLE 设备不会广播 advertisement, 扫描永远搜不到.
    // closeBLEConnection / closeBluetoothAdapter 都不能解除 iOS 系统层的 pair.
    // 唯一能用的路径: 用 bleInfo.deviceId 直接 wx.createBLEConnection 重连.
    //
    // 把 bleInfo 当成列表第一项 "上次连接 (S101)" 渲染, 用户点击就走 connectBle
    // 完整流程 (close-then-connect + 密钥核准 + forceEnableNotify), 绕过扫描限制.
    if (!self.staleAdded) {
      self.staleAdded = true;
      const stale: any = wx.getStorageSync('bleInfo');
      if (stale && stale.deviceId && stale.name) {
        const baseName = String(stale.name).replace(/(\s*\(上次连接\))+$/, '');
        self.bleArr.push({
          ...stale,
          name: baseName + ' (上次连接)',
          RSSI: typeof stale.RSSI === 'number' ? stale.RSSI : 0,
          mac: stale.mac || '',
          _stale: true, // v8: 标记 stale, 失败时自动剔除
        });
        self.setData({
          bleList: self.bleArr.slice(),
          statusText: '上次设备 ' + baseName + ' 已显示, 点击直接重连; 或等扫描其他设备…',
        });
      } else {
        self.setData({ statusText: '正在搜索附近的设备…', bleList: [] });
      }
    } else {
      self.setData({ statusText: `第 ${round} 轮扫描中… (附近无信号? 走近手表 1 米内试试)` });
    }

    veepooBle.veepooWeiXinSDKStartScanDeviceAndReceiveScanningDevice(function (res: any) {
      const device = res && res[0]
      if (!device || !device.name) return
      self.scanSeenCount++
      if (ENV.IS_TEST_BUILD) {
        console.log('[BLE scan]', device.name, 'RSSI=', device.RSSI, 'id=', device.deviceId, 'round=', self.data.scanRound)
      }
      if (typeof device.RSSI === 'number' && device.RSSI < ENV.MIN_BLE_RSSI) {
        self.refreshStatus(self.bleArr.length); return
      }
      const matched = ENV.IS_TEST_BUILD
        ? true
        : ENV.SUPPORTED_DEVICE_PREFIXES.some(p => device.name.startsWith(p))
      if (!matched) {
        self.refreshStatus(self.bleArr.length); return
      }
      self.scanMatchedCount++
      if (self.bleArr.find((d: any) => d.deviceId === device.deviceId)) {
        self.refreshStatus(self.bleArr.length); return
      }
      self.bleArr.push(device)
      self.setData({
        bleList: self.bleArr.slice().sort((a: any, b: any) => b.RSSI - a.RSSI)
      })
      self.refreshStatus(self.bleArr.length)
    })

    // 5.06-v8: 5s 早期诊断 — 任何 BLE 设备 (含非目标) 都没扫到 = 微信"附近的设备"权限
    // 没给 (Android 12+) 或手表完全没在广播. 客户实测日志根因 95% 是权限. 主动弹引导.
    if (self.earlyDiagTimer) clearTimeout(self.earlyDiagTimer)
    self.earlyDiagTimer = setTimeout(() => {
      if (self.earlyDiagShown) return
      // scanSeenCount 包括所有扫到的设备 (含被 RSSI/白名单 过滤的). 0 表示物理层就没收
      if (self.scanSeenCount > 0) return
      self.earlyDiagShown = true
      const isAndroid = !self.data.isIOS
      const title = isAndroid ? '蓝牙扫描没收到任何信号' : '蓝牙扫描没收到任何信号'
      const content = isAndroid
        ? '5 秒了周围一个蓝牙设备都没扫到, 大概率是微信"附近的设备"权限没开:\n\n' +
          '1. 手机系统设置 → 应用 → 微信 → 权限\n' +
          '2. 找到"附近的设备" / "蓝牙", 选"允许"\n' +
          '3. 完全划掉微信重新打开后扫码进来\n\n' +
          '如果权限已是允许, 请确认:\n' +
          '• 手表已开机 (摇一下亮屏看看)\n' +
          '• 手表没被其他手机连着 (在那台手机上断开/关蓝牙)\n' +
          '• 距离手表 1 米内'
        : '5 秒了周围一个蓝牙设备都没扫到, 请确认:\n\n' +
          '• 微信有蓝牙权限 (设置→隐私→蓝牙→允许微信)\n' +
          '• 手表已开机 + 没被其他手机连着\n' +
          '• 已在 iOS 设置→蓝牙 里点"忽略此设备" (如果之前连过)\n' +
          '• 距离手表 1 米内'
      wx.showModal({
        title,
        content,
        confirmText: '我去检查',
        cancelText: '继续扫描',
      })
    }, 5000)

    if (self.scanTimer) clearTimeout(self.scanTimer)
    self.scanTimer = setTimeout(() => {
      self.StopSearchBleManager()
      // 当前轮次新扫到设备 -> 停止, 让用户选
      if (self.scanMatchedCount > 0) {
        self.setData({ statusText: '', scanning: false })
        return
      }
      // 0 个新设备且还没到 MAX_SCAN_ROUNDS -> 静默续扫
      if (round < MAX_SCAN_ROUNDS) {
        console.log(`[bleConnection] 第 ${round} 轮空, 自动续扫第 ${round + 1} 轮`)
        setTimeout(() => self.startScanRound(), 500)
        return
      }
      // 两轮都没扫到 -> 停 + 弹引导
      self.setData({ scanning: false })
      const hasStale = self.bleArr.length > 0 && self.bleArr[0]._stale
      const summary = hasStale
        ? '只有"上次连接"那项可点; 没扫到新设备.'
        : '附近没有可连接的设备.'
      const iosTips =
        '常见原因 (iOS):\n1. 系统蓝牙残留 paired: 设置→蓝牙→找到 S101→点 "i" 图标→"忽略此设备", 然后回小程序重试\n2. 手表已被其他手机连着, 先在那台手机断开\n3. 距离太远 (1 米内最稳)\n4. 手表没开机或电量不足'
      const androidTips =
        '常见原因 (安卓):\n1. 手表已被其他手机连着, 先在那台手机断开\n2. 距离太远 (1 米内最稳)\n3. 手表没开机或电量不足\n4. 微信"附近的设备"权限没开'
      wx.showModal({
        title: '没搜到设备',
        content: summary + '\n\n' + (self.data.isIOS ? iosTips : androidTips),
        confirmText: '重新搜索',
        cancelText: '取消',
        success: (m: any) => {
          if (m.confirm) self.onRescanTap()
        },
      })
      self.setData({ statusText: '未搜到设备, 点上方"重新搜索"再试' })
    }, SCAN_TIMEOUT_MS)
  },

  // 兼容老调用路径
  veepooSDKGetSetting() { this.startScanRound() },

  // 状态行：用户面向语言，调试细节走 vConsole
  refreshStatus(listLen: number) {
    if (listLen > 0) {
      this.setData({ statusText: '' })
    } else {
      this.setData({ statusText: '正在搜索附近的设备…' })
    }
  },

  connectionDevice(e: any) {
    let self = this;
    let deviceList = self.data.bleList;
    wx.showLoading({
      title: '连接中'
    })
    this.StopSearchBleManager()
    deviceList.forEach((item: any) => {
      if (item.deviceId == e.currentTarget.dataset.deviceid) {
        wx.setStorageSync('bleInfo', item)
        // 连接
        veepooBle.veepooWeiXinSDKConnectionDevice(item, function (result: any) {
          wx.hideLoading()
          console.log("连接的result=>", result)
        })
      }
    })
  },

  connectBle(e: any) {
    const deviceId = e && e.currentTarget && e.currentTarget.dataset && e.currentTarget.dataset.deviceid
    if (!deviceId) return
    const item = this.data.bleList.find((d: any) => d.deviceId == deviceId)
    if (!item) {
      wx.showToast({ title: '设备已失效, 请重新搜索', icon: 'none' })
      return
    }
    this.connectBleInternal(item)
  },

  /**
   * 5.06-v8: 抽出来供 modal "重新连接" 直接复用, 不依赖 tap event.
   * stale 失效自动剔除: _stale=true 项点击后超时/失败 -> 从 bleList 删除 + 清 storage.bleInfo.
   * 握手超时弹 modal 而非 toast, 直接给重试按钮, 减少用户重进页面.
   * 5.5s 后判断 VPDevice 是否到位, 没到位不跳首页, 给 modal 选择重试 / 强行进首页.
   */
  connectBleInternal(item: any) {
    let self = this;
    const wasStale = item && item._stale === true
    const startedAt = Date.now()  // 5.06-v8 埋点: 连接耗时基准
    wx.showLoading({ title: '连接中', mask: true })
    self.StopSearchBleManager()

    wx.setStorageSync('bleInfo', item)
    wx.removeStorageSync('userDisconnected')
    wx.removeStorageSync('VPDevice')

    let settled = false;
    const failTimer = setTimeout(() => {
      if (settled) return;
      settled = true;
      wx.hideLoading();
      // 5.06-v8 埋点: 12s 握手超时
      dataStorage.reportBleEvent('connect_timeout', {
        success: false,
        durationMs: Date.now() - startedAt,
        errorMsg: wasStale ? 'stale-bleInfo' : 'sdk-connect-no-callback',
      });
      // v8: stale 项失败 -> 自动剔除 + 清 bleInfo, 防止用户反复点同一失效项
      if (wasStale) {
        self.bleArr = self.bleArr.filter((d: any) => d.deviceId !== item.deviceId)
        self.setData({ bleList: self.bleArr.slice() })
        wx.removeStorageSync('bleInfo')
        console.log('[bleConnection] stale 项连接超时, 已从列表剔除并清 bleInfo')
      }
      wx.showModal({
        title: '连接超时',
        content: wasStale
          ? '"上次连接"那只表已失效 (可能 deviceId 变了或表换走了), 已从列表移除. 请重新搜索.'
          : '蓝牙连接没成功. 常见原因:\n1. 手表已被其他手机连着\n2. 距离太远 (>2 米)\n3. iOS 系统残留 paired (设置→蓝牙→S101→忽略此设备)',
        confirmText: wasStale ? '重新搜索' : '重新连接',
        cancelText: '取消',
        success: (m: any) => {
          if (m.confirm) {
            if (wasStale) self.onRescanTap()
            else self.connectBleInternal(item)
          }
        },
      })
    }, HANDSHAKE_TIMEOUT_MS);

    const doSdkConnect = () => {
      veepooBle.veepooWeiXinSDKBleConnectionServicesCharacteristicsNotifyManager(item, async function (result: any) {
        console.log("result=>", result);
        if (settled) return;
        if (!result.connection) return; // 等下一次回调
        settled = true;
        clearTimeout(failTimer);

        // 订阅 notify (BleHub 已全局订阅, 这里把页面 listener 也加进去)
        self.notifyMonitorValueChange();

        // 5.06-v8 关键加固: forceEnableNotify 改成 Promise.all 等齐, 拿结果再发密钥核准.
        // 旧版本是 setTimeout 300ms 就发核准, 但 notify enable 是异步, 1s 才完成时
        // 核准发出去 type=1 永久丢. 现在 await 等 notify 全部 enable 后再发核准.
        const { bleHub } = require('../../services/bleHub');
        // 主动告知 BleHub "已连接" (防 iOS already-connected 短路 SDK connectionStateChange 不触发)
        bleHub.notifyConnected();
        let notifyResult = { enabled: 0, failed: 0, total: 0 };
        try {
          notifyResult = await bleHub.forceEnableNotify(item.deviceId);
          console.log('[bleConnection] forceEnableNotify 结果:', notifyResult);
        } catch (err) {
          console.warn('[bleConnection] forceEnableNotify 抛错', err);
        }
        // 诊断快照存 storage, 供 5.5s 后 modal 根因分流用
        wx.setStorageSync('bleConnDiag', {
          notifyEnabled: notifyResult.enabled,
          notifyFailed: notifyResult.failed,
          notifyTotal: notifyResult.total,
          passwordCheckCalls: 0,
          ts: Date.now(),
        });

        // 密钥核准 (SDK 回 type=1 含 VPDeviceMAC/Version, 由 BleHub.handleAutoSync 抓 mac 入 storage).
        // 重发 3 次 (notify 已 ready, 间隔 1s/1s 给表充足响应时间).
        const sendPasswordCheck = (label: string) => {
          try {
            veepooFeature.veepooBlePasswordCheckManager();
            console.log('[bleConnection] 密钥核准', label);
            const diag = wx.getStorageSync('bleConnDiag') || {};
            diag.passwordCheckCalls = (diag.passwordCheckCalls || 0) + 1;
            wx.setStorageSync('bleConnDiag', diag);
          } catch (e) {
            console.warn('[bleConnection] 密钥核准抛错', label, e);
          }
        };
        // notify ready 后立即发 #1 (旧版本要等 1.2s, 现在 0 延迟)
        sendPasswordCheck('#1');
        setTimeout(() => {
          if (wx.getStorageSync('VPDevice')) return;
          sendPasswordCheck('#2 (VPDevice 仍空)');
        }, 1500);
        setTimeout(() => {
          if (wx.getStorageSync('VPDevice')) return;
          sendPasswordCheck('#3 (VPDevice 仍空)');
        }, 3000);
        setTimeout(() => {
          try { bleHub.enableAutoMonitoring(); }
          catch (err) { console.warn('[bleConnection] enableAutoMonitoring 失败', err); }
        }, 2000);
        setTimeout(() => {
          try { bleHub.pullHistoryFromWatch(); }
          catch (err) { console.warn('[bleConnection] pullHistory 触发失败', err); }
        }, 4000);
        // 5.06-v8: 5s 后 VPDevice 仍空 -> 不跳首页, 弹根因分流 modal.
        // 旧版本是 5.5s 后盲跳首页, 用户看到 4 字段全空误以为坏了.
        // notify 已 ready 后核准间隔缩短为 0/1.5/3s, 5s 给最后一次核准 +2s 缓冲足够.
        setTimeout(() => {
          wx.hideLoading();
          const dev: any = wx.getStorageSync('VPDevice');
          const handshakeOk = dev && (dev.VPDeviceMAC || dev.VPDeviceVersion);
          if (!handshakeOk) {
            const diag: any = wx.getStorageSync('bleConnDiag') || {};
            console.warn('[bleConnection] 握手未完成, 诊断:', diag);
            // 5.06-v8 埋点: 握手失败 (5s 后 VPDevice 仍空)
            dataStorage.reportBleEvent('handshake_failed', {
              success: false,
              durationMs: Date.now() - startedAt,
              notifyEnabled: diag.notifyEnabled,
              notifyTotal: diag.notifyTotal,
              passwordCalls: diag.passwordCheckCalls,
              errorMsg: diag.notifyTotal === 0 ? 'no-notify-chars'
                : diag.notifyEnabled === 0 ? 'notify-all-failed'
                : diag.notifyEnabled < diag.notifyTotal ? 'notify-partial-fail'
                : !diag.passwordCheckCalls ? 'pwd-check-not-called'
                : 'no-type1-response',
            });
            // 根因分流: 按 notify 启用情况 + 密钥核准实际调用次数判断
            let title = '握手未完成';
            let content = '';
            if (diag.notifyTotal === 0) {
              title = 'BLE 服务初始化失败';
              content = '没找到任何 BLE notify 特征值 (notifyTotal=0). 多半是:\n• SDK 内部错, 重连一次\n• 手表蓝牙固件异常, 重启表试试';
            } else if (diag.notifyEnabled === 0) {
              title = 'notify 订阅全部失败';
              content = `notify 订阅 0/${diag.notifyTotal} 成功. 多半是 BLE 通道刚连上就被系统挂起或断开. 重连一次.`;
            } else if (diag.notifyEnabled < diag.notifyTotal) {
              title = 'notify 部分失败 + 表无响应';
              content = `notify 订阅 ${diag.notifyEnabled}/${diag.notifyTotal} 成功, 但表 3 次密钥核准都没回包.\n大概率手表已被其他手机连着 (BLE 一对一占用). 在那台手机上断开/关蓝牙后重试.`;
            } else if (!diag.passwordCheckCalls) {
              title = '密钥核准调用失败';
              content = 'SDK 内部错, 密钥核准函数没成功调用. 重连一次; 仍不行请重启小程序.';
            } else {
              title = '表无响应';
              content = `notify 已就绪 (${diag.notifyEnabled}/${diag.notifyTotal}), 密钥核准发了 ${diag.passwordCheckCalls} 次但表没回包.\n大概率手表已被其他手机连着 (BLE 一对一占用). 在那台手机上断开/关蓝牙后重试.`;
            }
            wx.showModal({
              title,
              content: content + '\n\n[诊断: notify=' + diag.notifyEnabled + '/' + diag.notifyTotal + ', 密钥发=' + (diag.passwordCheckCalls || 0) + ']',
              confirmText: '重新连接',
              cancelText: '强行进入',
              success: (m: any) => {
                if (m.confirm) self.connectBleInternal(item);
                else wx.redirectTo({ url: '/pages/index/index' });
              },
            });
            return;
          }
          // 5.06-v8 埋点: 连接成功 (VPDevice 有值, 握手完成)
          dataStorage.reportBleEvent('connect_success', {
            success: true,
            durationMs: Date.now() - startedAt,
            notifyEnabled: notifyResult.enabled,
            notifyTotal: notifyResult.total,
            passwordCalls: (wx.getStorageSync('bleConnDiag') || {}).passwordCheckCalls,
          });
          wx.redirectTo({ url: '/pages/index/index' });
        }, 5000);
      });
    };

    // 强制 close 一次再连. iOS CoreBluetooth 持有的 already-connected 状态
    // 是 SDK connect 短路的根因; close 后等 500ms 让系统真正释放.
    try {
      wx.closeBLEConnection({
        deviceId: item.deviceId,
        complete: (cr: any) => {
          console.log('[bleConnection] 重置: 已 close, 等 500ms 后 connect', cr && cr.errMsg);
          setTimeout(doSdkConnect, 500);
        },
      });
    } catch (e) {
      console.warn('[bleConnection] 重置 close 抛错, 直接 connect:', e);
      setTimeout(doSdkConnect, 500);
    }
  },
  
  connectBle2() {
    let self = this;
    wx.showLoading({
      title: '连接中'
    })
    this.StopSearchBleManager()
    let item = wx.getStorageSync('bleInfo')
    veepooBle.veepooWeiXinSDKBleConnectionServicesCharacteristicsNotifyManager(item, function (result: any) {
      console.log("result=>", result)
      if (result.connection) {
        if (item.name == 'DFULang') {
          wx.hideLoading()
          // 获取当前服务，订阅监听
          self.notifyMonitorValueChange();
          setTimeout(() => {
            wx.redirectTo({
              url: '/pages/index/index'
            })
          }, 1000);
          return
        }
        // 获取当前服务，订阅监听
        self.notifyMonitorValueChange();
        // 蓝牙密码核准
        veepooFeature.veepooBlePasswordCheckManager();

        wx.hideLoading()
        wx.redirectTo({
          url: '/pages/index/index'
        })
        return
        let times = setInterval(() => {
          // 设备芯片
          let deviceChip = wx.getStorageSync('deviceChip');
          // 当前设备芯片获取状态  （通过调用蓝牙密码核准获取）
          let deviceChipStatus = wx.getStorageSync('deviceChipStatus')
          console.log("deviceChipStatus==>", deviceChipStatus)
          console.log("deviceChip==>", deviceChip)
          if (deviceChipStatus) {
            wx.hideLoading()
            wx.redirectTo({
              url: '/pages/index/index'
            })

            // 实际业务流程根据获取到的芯片类型添加相关js逻辑
            // if (deviceChip == 1) {
            //   console.log("杰里");
            // } else if (deviceChip == 2) {
            //   console.log("炬芯")
            // } else if (deviceChip == 3) {
            //   console.log("中科")
            // } else {
            //   console.log("Nordic/汇顶系列")
            // }

            clearInterval(times)
          }
        }, 1000)
      }


    })
  },
  // 监听订阅 notifyMonitorValueChange
  notifyMonitorValueChange() {
    let self = this;
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange(function (e: any) {
      console.log("监听蓝牙回调= 这个是连接页面>", e);
      self.bleDataParses(e)
    })
  },
  // 5.06-v8: BleHub.installConnectionStateMonitor 已全局接管, 这个函数留作 no-op
  // 兼容老调用 (避免删除引用导致老页面崩). 不再注册 SDK callback 避免覆盖 BleHub.
  BLEConnectionStateChange() { /* no-op, 已迁移到 BleHub */ },
  // 停止蓝牙搜索
  StopSearchBleManager() {
    veepooBle.veepooWeiXinSDKStopSearchBleManager(function (e: any) {
      console.log("停止蓝牙搜索=>", e)
    })
  },
  // 密钥核验  无参数
  BlePasswordCheckManager() {
    veepooFeature.veepooBlePasswordCheckManager()
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
  // 监听蓝牙返回数据解析
  bleDataParses(value: any) {
    let content = value;
    console.log("content=>", content)
    // 全局兜底：连接成功但跳转 index 之间的极短窗口若有数据推送也能落库 + 上传
    dispatchBleData(value)
  }
})