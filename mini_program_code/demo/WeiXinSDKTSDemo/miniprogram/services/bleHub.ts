/**
 * BLE 全局事件中心 (BleHub)
 *
 * 解决两个核心问题:
 *   (1) Veepoo SDK 的 notifyMonitorValueChange 是 single-listener,
 *       多个页面同时订阅会互相覆盖, 全局监听拿不到事件.
 *       BleHub monkey-patch SDK 函数, 让所有调用变成注册到 hub,
 *       hub 再统一向 SDK 注册一次真实回调, 多 listener 共存.
 *
 *   (2) 用户在手表上测量后, 不会主动打开小程序对应页面去采集.
 *       必须有一个全局兜底:
 *         - 实时推送阶段: 不论用户在哪个页面, hub 自动 saveData
 *         - 离线缓存阶段: 重连成功时, hub 主动调用 SDK 拉历史
 *           (Veepoo 手表本地缓存最近 3 天日常数据)
 *
 * 顺带做的事:
 *   - 从 type=1 (蓝牙密码核准) 回调里抓取 VPDeviceMAC, 写入 bleInfo.mac
 *     供 dataStorage.resolveDeviceId() 用真 MAC 当 device_sign,
 *     避免 iOS UUID 飘逸导致同表多行.
 */

import { veepooBle, veepooFeature } from '../miniprogram_dist/index';
import { dataStorage } from './dataStorage';
import { HealthDataType } from '../types/healthData';

type Listener = (e: any) => void;

class BleHub {
  private listeners: Listener[] = [];
  private ecgListeners: Listener[] = [];
  private initialized = false;
  private lastPullAt = 0;

  // 5.06-v8 稳态保障: 全局连接状态 + 心跳 + 重连指数退避
  private adapterAvailable = true;        // wx 蓝牙适配器是否可用 (用户没关蓝牙)
  private bleConnected = false;           // 当前是否有 BLE 物理连接
  private lastPacketAt = 0;               // 上次收到任何 type=N 回包的时刻 (心跳判断暗断)
  private heartbeatTimer: any = null;     // 30s 心跳定时器
  private reconnectAttempt = 0;           // 当前重连重试计数 (1/2/3)
  private reconnectTimer: any = null;     // 退避 setTimeout 句柄, 同时也是 "重连进行中" 互斥锁
  private reconnectInProgress = false;    // 同上, setTimeout 触发回调期间也算进行中

  init(): void {
    if (this.initialized) return;
    this.initialized = true;

    // 用 BLECharacteristicValueChangeManager 而非 NotifyMonitorValueChange 注册 SDK 解析器:
    //   后者会先 wx.notifyBLECharacteristicValueChange 启用 SDK 特定 service/char 的 notify,
    //   而 app.onLaunch 时 bleInfo.deviceId 为空 -> 必失败 -> success cb 内的
    //   wx.onBLECharacteristicValueChange 永不注册 -> Veepoo 协议响应全丢.
    // BLECharacteristicValueChangeManager 仅注册 wx 全局监听 + 走解析器, 不依赖连接状态.
    // BLE notify 由 forceEnableNotify 在连接成功后兜底启用, 解耦.
    veepooBle.veepooWeiXinSDKBLECharacteristicValueChangeManager((e: any) => {
      // SDK 解析失败时偶尔传 undefined, 过滤掉避免 listener 里访问 e.type 抛 TypeError.
      if (!e || typeof e !== 'object' || typeof e.type === 'undefined') return;
      // 同一份 SDK 解析事件:
      //   - 普通体征/设置类 -> monitor listeners
      //   - type=51 心率 ECG 通道也要 (handleEcgChannel 兜底入库)
      this.dispatch(this.listeners, e);
      if (e.type === 51) this.dispatch(this.ecgListeners, e);
    });

    // monkey-patch 让旧的页面调用都进 hub listener 队列 (避免页面再调 SDK 原生
    // 又触发一次 wx.onBLECharacteristicValueChange 注册, 与 fan-out 之外的覆盖).
    veepooBle.veepooWeiXinSDKNotifyMonitorValueChange = (cb: Listener) => { this.listeners.push(cb); };
    veepooBle.veepooWeiXinSDKNotifyECGValueChange = (cb: Listener) => { this.ecgListeners.push(cb); };

    // 诊断 listener: 把每条 type=N 回包打印出来, 方便排查未识别的数据类型.
    // 数组型 content (手动测量批量) 单独处理: 打 length + 首条 sample, 方便对照协议.
    this.listeners.push((e: any) => {
      if (!e || typeof e.type === 'undefined') return;
      const c = e.content;
      const dt = e.dataType !== undefined ? ` dt=${e.dataType}` : '';
      if (Array.isArray(c)) {
        const sample = c.length ? JSON.stringify(c[0]).slice(0, 200) : '';
        console.log(`[BleHub] type=${e.type}${dt} content=Array(len=${c.length}) sample=${sample}`);
      } else if (c && typeof c === 'object') {
        const fields = Object.keys(c)
          .filter(k => typeof c[k] === 'number' || typeof c[k] === 'string')
          .slice(0, 8)
          .map(k => `${k}=${JSON.stringify(c[k])}`)
          .join(' ');
        console.log(`[BleHub] type=${e.type}${dt} ${fields}`);
      } else {
        console.log(`[BleHub] type=${e.type}${dt} content=${JSON.stringify(c)}`);
      }
    });
    this.listeners.push((e: any) => this.handleAutoSync(e));
    this.ecgListeners.push((e: any) => this.handleEcgChannel(e));

    // 5.06-v8: 任何 type=N 都更新 lastPacketAt, 心跳 90s 静默 → 视为暗断触发重连
    this.listeners.push((e: any) => {
      if (e && typeof e.type !== 'undefined') {
        this.lastPacketAt = Date.now();
      }
    });

    // 5.06-v8: 全局适配器/连接状态监听 + 30s 心跳
    this.installAdapterStateMonitor();
    this.installConnectionStateMonitor();
    this.startHeartbeat();

    console.log('[BleHub] 全局事件中心已初始化 (monitor+ecg 两通道, 单 wx listener via 全局 fan-out)');
  }

  /**
   * 5.06-v8 稳态 #8: 全局监听蓝牙适配器开关.
   * 用户在系统设置/控制中心切了飞行模式 / 关蓝牙 → BLE 通道断, 但 SDK 本身不主动告知;
   * 旧版本只在扫描页 (体验版) 注册了这个事件, 首页用户根本看不到提示.
   * 现在全局注册, 关蓝牙 toast 提示, 重开蓝牙自动触发重连.
   */
  private installAdapterStateMonitor(): void {
    try {
      wx.onBluetoothAdapterStateChange((res: any) => {
        const wasAvailable = this.adapterAvailable;
        this.adapterAvailable = !!(res && res.available);
        console.log('[BleHub] adapter state', res);
        if (wasAvailable && !this.adapterAvailable) {
          // 蓝牙刚被关 / 飞行模式
          wx.showToast({ title: '蓝牙已关闭, 数据无法同步', icon: 'none', duration: 3000 });
          this.bleConnected = false;
          dataStorage.resetDeviceIdCache();
          dataStorage.reportBleEvent('adapter_off', { success: false });
        } else if (!wasAvailable && this.adapterAvailable) {
          // 蓝牙刚被重开 → 触发自动重连
          wx.showToast({ title: '蓝牙已恢复, 正在重连…', icon: 'none', duration: 2000 });
          dataStorage.reportBleEvent('adapter_on', { success: true });
          this.requestReconnect('adapter-recovered');
        }
      });
    } catch (e) {
      console.warn('[BleHub] adapter state monitor 注册失败', e);
    }
  }

  /**
   * 5.06-v8 稳态 #9: 全局监听 BLE 连接状态变化, 主动断开除外都自动重连.
   * connectionStateChange.connected=false 路径:
   *   - 距离过远 / 走出范围 (常见, 但可能很快又能连上)
   *   - 表换电池 / 重启
   *   - 用户主动按 "断开连接" 按钮 (设了 storage.userDisconnected=true, 跳过)
   *   - 系统挂起小程序后台过久, BLE session 被释放
   */
  private installConnectionStateMonitor(): void {
    try {
      veepooBle.veepooWeiXinSDKBLEConnectionStateChangeManager((e: any) => {
        const wasConnected = this.bleConnected;
        this.bleConnected = !!(e && e.connected);
        console.log('[BleHub] connection state', e);
        if (wasConnected && !this.bleConnected) {
          dataStorage.resetDeviceIdCache();
          dataStorage.reportBleEvent('connection_lost', { success: false });
          if (!wx.getStorageSync('userDisconnected')) {
            this.requestReconnect('connection-lost');
          }
        }
      });
    } catch (e) {
      console.warn('[BleHub] connection state monitor 注册失败', e);
    }
  }

  /**
   * 5.06-v8 稳态 #10: 30s 心跳 (复用 SDK 电量请求, 顺便刷首页电量字段).
   * BLE 通道暗断检测: 上次收到任何 type=N 回包超过 90s → 视为通道挂了, 触发重连.
   * 暗断常见于: 距离过远 / 微信后台被系统挂起 / 信号被其他蓝牙干扰.
   * connection state callback 不一定能感知到 (尤其 iOS), 心跳兜底.
   */
  private startHeartbeat(): void {
    if (this.heartbeatTimer) clearInterval(this.heartbeatTimer);
    this.heartbeatTimer = setInterval(() => {
      if (!this.bleConnected || !this.adapterAvailable) return;
      // 发电量请求当心跳, 表收到会回 type=2 (含 VPDeviceElectricPercent), 顺带刷首页
      try {
        veepooFeature.veepooReadElectricQuantityManager();
      } catch (e) {
        console.warn('[BleHub] heartbeat readElectric 抛错', e);
      }
      // 90s 内无任何 type=N 回包 → 通道暗断
      if (this.lastPacketAt > 0 && Date.now() - this.lastPacketAt > 90000) {
        console.warn('[BleHub] 心跳: 90s 无回包, 视为通道暗断, 触发重连');
        this.bleConnected = false;
        this.lastPacketAt = 0;
        dataStorage.reportBleEvent('heartbeat_timeout', { success: false });
        this.requestReconnect('heartbeat-timeout');
      }
    }, 30000);
  }

  /**
   * 5.06-v8 稳态 #9: 重连入口 (集中式), 指数退避 1s/2s/4s 三次.
   * 多个触发源都调这个: adapter recovered / connection-lost / heartbeat-timeout / app.onShow.
   * 互斥锁保证同周期内不重复发起 (reconnectTimer + reconnectInProgress 双重保险).
   * 三次都失败 → toast 提示用户手动到设备扫描页, 不无限重试浪费电.
   */
  /**
   * 5.06-v8: 外部 (connectBleInternal 成功路径) 主动告知 "已连接".
   * 防 SDK connection state callback 在 iOS already-connected 短路场景下不触发,
   * 导致 BleHub.bleConnected 仍是 false → 心跳不发 → 暗断检测假阴.
   */
  notifyConnected(): void {
    this.bleConnected = true;
    this.lastPacketAt = Date.now();
    this.reconnectAttempt = 0;
    this.reconnectInProgress = false;
    if (this.reconnectTimer) { clearTimeout(this.reconnectTimer); this.reconnectTimer = null; }
    console.log('[BleHub] notifyConnected: bleConnected=true');
  }

  requestReconnect(reason: string): void {
    const bleInfo: any = wx.getStorageSync('bleInfo');
    const userDisconnected = wx.getStorageSync('userDisconnected');
    if (!bleInfo || !bleInfo.deviceId) {
      console.log('[BleHub] requestReconnect skip: 无 bleInfo, reason=' + reason);
      return;
    }
    if (userDisconnected) {
      console.log('[BleHub] requestReconnect skip: 用户已主动断开');
      return;
    }
    if (this.reconnectInProgress || this.reconnectTimer) {
      console.log('[BleHub] requestReconnect skip: 已有重连进行中');
      return;
    }
    if (!this.adapterAvailable) {
      console.log('[BleHub] requestReconnect skip: 蓝牙适配器不可用');
      return;
    }
    console.log(`[BleHub] requestReconnect 触发, reason=${reason}`);
    this.reconnectAttempt = 0;
    this.tryReconnectOnce(bleInfo);
  }

  private tryReconnectOnce(bleInfo: any): void {
    this.reconnectAttempt++;
    const attempt = this.reconnectAttempt;
    const startedAt = Date.now();  // 5.06-v8 埋点: 重连耗时
    this.reconnectInProgress = true;
    console.log(`[BleHub] 重连尝试 #${attempt}`);
    try {
      veepooBle.veepooWeiXinSDKBleReconnectDeviceManager(bleInfo, async (res: any) => {
        console.log(`[BleHub] 重连 #${attempt} =>`, res);
        const ok = res && (res.connection === true || res.reconnect === true);
        if (ok) {
          this.reconnectAttempt = 0;
          this.reconnectInProgress = false;
          this.bleConnected = true;
          this.lastPacketAt = Date.now();
          // forceEnableNotify Promise.all 等齐 + 立即发密钥核准 + autoMonitoring + pullHistory
          let notifyResult = { enabled: 0, failed: 0, total: 0 };
          try {
            notifyResult = await this.forceEnableNotify(bleInfo.deviceId);
            console.log('[BleHub] reconnect forceEnableNotify 结果:', notifyResult);
          } catch (err) {
            console.warn('[BleHub] reconnect forceEnableNotify 抛错', err);
          }
          try { veepooFeature.veepooBlePasswordCheckManager(); }
          catch (e) { console.warn('[BleHub] reconnect 密钥核准抛错', e); }
          setTimeout(() => this.enableAutoMonitoring(), 1000);
          setTimeout(() => this.pullHistoryFromWatch(), 2000);
          // 5.06-v8 埋点: 重连成功
          dataStorage.reportBleEvent('reconnect_success', {
            success: true,
            durationMs: Date.now() - startedAt,
            notifyEnabled: notifyResult.enabled,
            notifyTotal: notifyResult.total,
            errorMsg: `attempt=${attempt}`,
          });
          return;
        }
        // 失败 → 指数退避 1s/2s/4s 三次
        if (attempt < 3) {
          const delay = Math.pow(2, attempt - 1) * 1000;
          console.log(`[BleHub] 重连 #${attempt} 失败, ${delay}ms 后重试`);
          this.reconnectTimer = setTimeout(() => {
            this.reconnectTimer = null;
            this.tryReconnectOnce(bleInfo);
          }, delay);
        } else {
          console.warn('[BleHub] 3 次重连全失败, 放弃, 等下次触发');
          this.reconnectAttempt = 0;
          this.reconnectInProgress = false;
          // 5.06-v8 埋点: 重连最终失败 (3 次都没连上)
          dataStorage.reportBleEvent('reconnect_failed', {
            success: false,
            durationMs: Date.now() - startedAt,
            errorMsg: 'all-3-attempts-failed',
          });
          wx.showToast({
            title: '蓝牙重连失败, 请到设备扫描页手动重连',
            icon: 'none',
            duration: 3000,
          });
        }
      });
    } catch (err) {
      console.warn('[BleHub] reconnect 调用抛错', err);
      this.reconnectInProgress = false;
      if (attempt < 3) {
        const delay = Math.pow(2, attempt - 1) * 1000;
        this.reconnectTimer = setTimeout(() => {
          this.reconnectTimer = null;
          this.tryReconnectOnce(bleInfo);
        }, delay);
      } else {
        this.reconnectAttempt = 0;
      }
    }
  }

  private dispatch(list: Listener[], e: any): void {
    for (const fn of list) {
      try { fn(e); } catch (err) { console.warn('[BleHub] listener 异常', err); }
    }
  }

  // 把 type=1/2/9 抓到的设备状态合并写入 VPDevice storage,
  // 首页 BlePasswordCheckManager 检测到 VPDevice 就自动填 UI, 不依赖 listener 注册时机.
  private updateDeviceSnapshot(updates: Record<string, any>): void {
    const snap: any = wx.getStorageSync('VPDevice') || {};
    const bleInfo: any = wx.getStorageSync('bleInfo') || {};
    if (bleInfo.name && !snap.name) snap.name = bleInfo.name;
    let changed = false;
    for (const k of Object.keys(updates)) {
      if (typeof updates[k] === 'undefined') continue;
      if (snap[k] !== updates[k]) { snap[k] = updates[k]; changed = true; }
    }
    if (changed) {
      wx.setStorageSync('VPDevice', snap);
      console.log('[BleHub] VPDevice 快照更新:', updates);
    }
  }

  /**
   * 手动测量历史批量回包: type=53/55/56, content 是数组.
   * 用户在表上点 "开始测量" 完成的体征 (血压/心率/血氧/体温/血糖/HRV/血液成分),
   * 表本地缓存; pullHistoryFromWatch 调 SDK 拉历史时, 表用这个批量接口回数据.
   *
   * 字段名按 SDK 文档示例兼容多种命名:
   *   血压 dataType=0:
   *     BasicData: {high, low, heartRate, status, credibility} 或
   *     {bloodPressureHigh, bloodPressureLow, heartRate}
   *   心率 dataType=1:
   *     BasicData: {heartRate} 或 {heart}
   *   血糖 dataType=2:
   *     BasicData: {bloodGlucose} 或 {bloodSugar}
   *   血氧 dataType=4:
   *     BasicData: {bloodOxygen, heartRate} 或 {oxygen, heart}
   *   体温 dataType=5:
   *     BasicData: {bodyTemperature, skinTemperature} 或 {temperature}
   *   微体检 (顶层结构, 不是 BasicData):
   *     {heart, oxygen, bloodSugar, bodyTemperature, highPressure, lowPressure, hrv, ...}
   */
  private handleManualMeasurementBatch(e: any): void {
    const c = e.content;
    const dt = e.dataType;
    if (!Array.isArray(c) || c.length === 0) {
      console.log(`[BleHub] 手动测量批量 type=${e.type} dataType=${dt} 空数组, 跳过`);
      return;
    }
    console.log(`[BleHub] 手动测量批量 type=${e.type} dataType=${dt} 共 ${c.length} 条, 开始落库`);

    for (const item of c) {
      const basic = item.BasicData || item.basicData || item;
      let savedAs: string | null = null;

      // 血压
      if (dt === 0) {
        const high = (typeof basic.high === 'number' ? basic.high :
          (typeof basic.bloodPressureHigh === 'number' ? basic.bloodPressureHigh :
          (typeof basic.systolic === 'number' ? basic.systolic :
          (typeof basic.highPressure === 'number' ? basic.highPressure : undefined))));
        const low = (typeof basic.low === 'number' ? basic.low :
          (typeof basic.bloodPressureLow === 'number' ? basic.bloodPressureLow :
          (typeof basic.diastolic === 'number' ? basic.diastolic :
          (typeof basic.lowPressure === 'number' ? basic.lowPressure : undefined))));
        if (typeof high === 'number' || typeof low === 'number') {
          dataStorage.saveData('bloodPressure', {
            systolic: high || 0,
            diastolic: low || 0,
            heartRate: basic.heartRate || basic.heart || 0,
            measureStatus: basic.status || 0,
          });
          savedAs = 'bloodPressure';
        }
      }
      // 心率
      else if (dt === 1) {
        const hr = typeof basic.heartRate === 'number' ? basic.heartRate :
          (typeof basic.heart === 'number' ? basic.heart : undefined);
        if (typeof hr === 'number' && hr > 0) {
          dataStorage.saveData('heartRate', { heartRate: hr, heartState: basic.heartState || 0 });
          savedAs = 'heartRate';
        }
      }
      // 血糖
      else if (dt === 2) {
        const bg = typeof basic.bloodGlucose === 'number' ? basic.bloodGlucose :
          (typeof basic.bloodSugar === 'number' ? basic.bloodSugar :
          (typeof basic.glucose === 'number' ? basic.glucose : undefined));
        if (typeof bg === 'number' && bg > 0) {
          dataStorage.saveData('bloodGlucose', { bloodGlucose: bg, measureTime: basic.measureTime || '' });
          savedAs = 'bloodGlucose';
        }
      }
      // 血氧
      else if (dt === 4) {
        const ox = typeof basic.bloodOxygen === 'number' ? basic.bloodOxygen :
          (typeof basic.oxygen === 'number' ? basic.oxygen :
          (typeof basic.spo2 === 'number' ? basic.spo2 : undefined));
        if (typeof ox === 'number' && ox > 0) {
          dataStorage.saveData('bloodOxygen', {
            bloodOxygen: ox,
            heartRate: basic.heartRate || basic.heart || 0,
            allDayData: [],
          });
          savedAs = 'bloodOxygen';
        }
      }
      // 体温
      else if (dt === 5) {
        const tp = typeof basic.bodyTemperature === 'number' ? basic.bodyTemperature :
          (typeof basic.temperature === 'number' ? basic.temperature : undefined);
        if (typeof tp === 'number' && tp > 0) {
          dataStorage.saveData('temperature', {
            bodyTemperature: tp,
            skinTemperature: basic.skinTemperature || basic.bodySurfaceTemperature || 0,
            temperatureUnit: 'celsius',
          });
          savedAs = 'temperature';
        }
      }
      // HRV / 血液成分 / 微体检 暂时只记录, 上传维度服务端尚未规划

      if (savedAs) {
        console.log(`[BleHub] 手动测量入库 dataType=${dt} -> ${savedAs} (ts=${item.timestamp || basic.timestamp})`);
      } else {
        console.warn(`[BleHub] 手动测量未落库 dataType=${dt} item=`, JSON.stringify(item).slice(0, 200));
      }
    }
  }

  /**
   * ECG 通道自动同步:
   *   - type=51 心率值 -> heartRate 表
   *   - 'ecg波形数据' 不在这里 saveData (波形需要累积, 由 ecgTest 页面处理)
   *   - 'ecg测量' 完成 (progress=100) 不在这里 saveData (同上, 缺 totalArray)
   */
  private handleEcgChannel(e: any): void {
    if (!e || typeof e.type === 'undefined') return;
    if (e.type !== 51) return;
    const hr = e.content?.heartRate;
    if (typeof hr !== 'number' || hr === 0) return;
    console.log(`[BleHub] ECG 通道自动同步 type=51 -> heartRate=${hr}`);
    dataStorage.saveData('heartRate', { heartRate: hr, heartState: e.content?.heartState || 0 });
  }

  /**
   * 自动同步路由
   * 仅处理 "手表主动测量产生的体征数据" 与 type=1 的 MAC 抓取,
   * 设置类回调 (type 2/10/11/12/13/17/19/20/24/25/26/28/90 等) 直接忽略.
   */
  private handleAutoSync(e: any): void {
    if (!e || typeof e.type === 'undefined') return;
    const c = e.content || {};

    if (e.type === 1) {
      const mac = c.VPDeviceMAC;
      if (mac && typeof mac === 'string') {
        const info: any = wx.getStorageSync('bleInfo') || {};
        if (info.mac !== mac) {
          info.mac = mac;
          wx.setStorageSync('bleInfo', info);
          dataStorage.resetDeviceIdCache();
          console.log(`[BleHub] 捕获手表 MAC=${mac}, 已写入 bleInfo.mac`);
        }
        // 5.06-v6: mac 已就绪 -> 立即触发 flushPending, 把 type=1 之前堆积的 deviceId=-1
        // 队列项重传. flushPending 内部会重新调 resolveDeviceId 拿真 deviceId.
        // 队列空则 no-op. 这是 deviceId=-1 队列项的唯一出路.
        void dataStorage.flushPending().then((r: any) => {
          if (r.ok > 0 || r.fail > 0) {
            console.log(`[BleHub] type=1 触发 flushPending: ok=${r.ok} fail=${r.fail}`);
          }
        });
      }
      // 同步把 type=1 全字段写入 VPDevice storage, 让首页 onShow 直接读取兜底
      // (避免首页 listener 注册晚于 type=1 回包导致 MAC/版本永空).
      this.updateDeviceSnapshot({
        VPDeviceMAC: c.VPDeviceMAC,
        VPDeviceVersion: c.VPDeviceVersion,
      });
      return;
    }
    if (e.type === 2 && typeof c.VPDeviceElectricPercent !== 'undefined') {
      this.updateDeviceSnapshot({ VPDeviceElectricPercent: c.VPDeviceElectricPercent });
    }
    if (e.type === 9 && typeof c.step !== 'undefined') {
      this.updateDeviceSnapshot({ step: c.step, calorie: c.calorie, distance: c.distance });
    }

    // 手动测量历史批量回包 (pullHistoryFromWatch 调 veepooSendManualMeasurementDataReadManager 的结果).
    // SDK 文档说 type=53, 实际 SDK 版本看到也有 type=55 / 56 等变种, 都按数据数组处理.
    // content = Array<{timestamp, BasicData:{high, low, heartRate,...}, UserData}>
    // 用户在表上手动按"开始测量"得到的血压/心率/血糖/血氧/体温, 表自己不主动 push type=18/30 等
    // 实时回包, 必须靠这个批量接口才能拿回来 — 没 case 53 的话血压等永远落不到库.
    if (e.type === 53 || e.type === 55 || e.type === 56) {
      this.handleManualMeasurementBatch(e);
      return;
    }

    let dataType: HealthDataType | null = null;
    let payload: any = null;

    switch (e.type) {
      case 9:
        dataType = 'step';
        payload = {
          step: c.step || 0,
          calorie: c.calorie || 0,
          distance: c.distance || 0,
        };
        break;

      case 18:
        if (typeof c.bloodPressureHigh !== 'number' && typeof c.bloodPressureLow !== 'number') return;
        dataType = 'bloodPressure';
        payload = {
          systolic: c.bloodPressureHigh || 0,
          diastolic: c.bloodPressureLow || 0,
          heartRate: c.heartRate || 0,
          measureStatus: c.measureStatus || 0,
        };
        break;

      case 51:
        if (typeof c.heartRate !== 'number') return;
        dataType = 'heartRate';
        payload = {
          heartRate: c.heartRate || 0,
          heartState: c.heartState || 0,
        };
        break;

      case 6:
      case 7:
        if (typeof c.bodyTemperature !== 'number' || c.bodyTemperature === 0) return;
        dataType = 'temperature';
        payload = {
          bodyTemperature: c.bodyTemperature,
          skinTemperature: c.skinTemperature || 0,
          temperatureUnit: c.temperatureUnit || 'celsius',
        };
        break;

      case 30:
      case 31:
        if (typeof c.bloodOxygen !== 'number' || c.bloodOxygen === 0) return;
        dataType = 'bloodOxygen';
        payload = {
          bloodOxygen: c.bloodOxygen,
          heartRate: c.heartRate || 0,
          allDayData: c.allDayData || [],
        };
        break;

      case 37:
      case 38:
        if (typeof c.bloodGlucose !== 'number' || c.bloodGlucose === 0) return;
        dataType = 'bloodGlucose';
        payload = {
          bloodGlucose: c.bloodGlucose,
          measureTime: c.measureTime || '',
        };
        break;

      case 39:
      case 40:
        dataType = 'bloodLiquid';
        payload = {
          uricAcid: c.uricAcid || 0,
          cholesterol: c.cholesterol || 0,
          triacylglycerol: c.triacylglycerol || 0,
          highDensity: c.highDensity || 0,
          lowDensity: c.lowDensity || 0,
        };
        break;

      case 32:
        if (typeof c.weight !== 'number' || c.weight === 0) return;
        dataType = 'bodyComposition';
        payload = {
          weight: c.weight,
          bmi: c.bmi || 0,
          bodyFatRate: c.bodyFatRate || 0,
          muscleRate: c.muscleRate || 0,
          moisture: c.moisture || 0,
          boneMass: c.boneMass || 0,
          visceralFat: c.visceralFat || 0,
          basalMetabolism: c.basalMetabolism || 0,
          proteinRate: c.proteinRate || 0,
          bodyAge: c.bodyAge || 0,
        };
        break;

      case 42:
        if (e.Progress !== 100 && e.progress !== 100) return;
        dataType = 'ecg';
        payload = {
          heartRate: c.heartRate || 0,
          hrvValue: c.hrvValue || 0,
          diseaseResult: c.diseaseResult || [],
          measureDuration: c.measureDuration || 0,
        };
        break;

      case 4:
        if (!c.fallAsleepTime && !c.wakeUpTime) return;
        dataType = 'sleep';
        payload = {
          fallAsleepTime: c.fallAsleepTime || '',
          wakeUpTime: c.wakeUpTime || '',
          deepSleepTime: c.deepSleepTime || 0,
          lightSleepTime: c.lightSleepTime || 0,
          sleepQuality: c.sleepQuality || 0,
          sleepCurve: c.sleepCurve || [],
        };
        break;

      case 5:
        if ((e.Progress !== 100 && e.progress !== 100) || !Array.isArray(c)) return;
        dataType = 'daily';
        payload = { dailyRecords: c };
        break;

      default:
        return;
    }

    if (dataType && payload) {
      console.log(`[BleHub] 自动同步 type=${e.type} -> ${dataType}`);
      dataStorage.saveData(dataType, payload);
    }
  }

  /**
   * 自动监测开关一键打开 (心率 / 血氧 / 体温).
   * 设备出厂可能默认开, 也可能被用户/上次设置关掉. 不依赖默认值, 直接强写一次.
   *
   * 心率 + 体温 走 veepooSendAutoTestSwitchDataManager (一次设多个开关);
   * 血氧 单独 API veepooSendBloodOxygenAutoTestDataManager (要带时段, 设全天 00:00-23:59).
   *
   * 调用时机: BLE 密钥核准成功 (type=1 收到) 之后, 太早调 SDK 会以 deviceId 缺失失败.
   * 推荐放在 connect 成功 + 密钥核准 #1 后 ~3s.
   */
  enableAutoMonitoring(): void {
    try {
      veepooFeature.veepooSendAutoTestSwitchDataManager({
        heartRate: 'start',
        bodyTemperature: 'start',
      });
      console.log('[BleHub] 自动监测开关 -> 心率/体温 已写 start');
    } catch (e) {
      console.warn('[BleHub] AutoTestSwitch 写入失败:', e);
    }
    try {
      veepooFeature.veepooSendBloodOxygenAutoTestDataManager({
        switch: 'start',
        startTime: '00:00',
        endTime: '23:59',
        deviceControl: 'setup',
      });
      console.log('[BleHub] 自动监测开关 -> 血氧 已写 start (00:00-23:59 全天)');
    } catch (e) {
      console.warn('[BleHub] BloodOxygenAutoTest 写入失败:', e);
    }
  }

  /**
   * 强制订阅所有 notify 特征值. 修 iOS already-connected 场景:
   *   SDK connect API 看到 BLE 已连就短路返回 result.connection=true,
   *   但内部跳过 wx.notifyBLECharacteristicValueChange 步骤
   *   -> SDK 后续 password check / 电量 / 步数请求 write 出去,
   *      但 notify 没开 -> type=1/2/9 回包全丢 -> 首页 MAC/版本/电量/步数永空.
   *
   * 通过 wx 原生 API 显式遍历服务/特征, 强制 enable 所有 notify.
   * 已订阅的会返回 errno=10008 但不影响 (已经开着).
   *
   * 5.06-v8 关键加固: 改成 Promise + Promise.all 等待所有 notify 真的 enable 完成.
   * 旧版本是 fire-and-forget, 调用方 setTimeout 300ms 后就发密钥核准, 但 notify
   * enable 是异步, 慢的 service (尤其 iOS / S101 双协议) 可能 1s 才 enable 完成,
   * 导致密钥核准发出去时 notify 仍未 ready -> type=1 永久丢失 -> 首页 4 字段空.
   * 现在调用方 await 拿到结果, 拿到 enabled 数量再决定继续还是诊断报错.
   *
   * 返回 { enabled, failed, total }: total 是所有 notify 特征数;
   * enabled 是真正成功 (含 errCode=10008 已订阅, 视为成功);
   * failed 是真失败. enabled+failed===total.
   */
  forceEnableNotify(deviceId: string): Promise<{ enabled: number; failed: number; total: number }> {
    return new Promise((resolve) => {
      if (!deviceId) {
        resolve({ enabled: 0, failed: 0, total: 0 });
        return;
      }
      wx.getBLEDeviceServices({
        deviceId,
        success: (sRes: any) => {
          const services = sRes.services || [];
          console.log('[forceEnableNotify] services count:', services.length);
          // 第一阶段: 拉所有 service 的 characteristics (并行 + Promise.all 等齐)
          Promise.all(services.map((svc: any) => new Promise<{svc: string; ch: string}[]>((res) => {
            wx.getBLEDeviceCharacteristics({
              deviceId, serviceId: svc.uuid,
              success: (cRes: any) => {
                const notifyChars = (cRes.characteristics || [])
                  .filter((ch: any) => ch.properties && ch.properties.notify)
                  .map((ch: any) => ({ svc: svc.uuid, ch: ch.uuid }));
                res(notifyChars);
              },
              fail: (e: any) => {
                console.warn('[forceEnableNotify] getCharacteristics fail', svc.uuid.slice(0, 8), e.errMsg || e);
                res([]);
              },
            });
          }))).then((charLists) => {
            const allChars = charLists.flat();
            const total = allChars.length;
            if (total === 0) {
              console.warn('[forceEnableNotify] 没有任何 notify 特征值, SDK 内部错或 BLE 物理层未连接');
              resolve({ enabled: 0, failed: 0, total: 0 });
              return;
            }
            // 第二阶段: 并行 enable 所有 notify, Promise.all 等齐
            let enabled = 0;
            let failed = 0;
            Promise.all(allChars.map(({ svc, ch }) => new Promise<void>((res) => {
              wx.notifyBLECharacteristicValueChange({
                state: true, deviceId, serviceId: svc, characteristicId: ch,
                success: () => {
                  enabled++;
                  console.log('[forceEnableNotify] ok', svc.slice(0, 8), ch.slice(0, 8));
                  res();
                },
                fail: (e: any) => {
                  // errCode=10008 是"已订阅", 算成功 (实际 notify 是开着的)
                  if (e && (e.errCode === 10008 || /already.*notify/i.test(e.errMsg || ''))) {
                    enabled++;
                    console.log('[forceEnableNotify] ok (already enabled)', svc.slice(0, 8), ch.slice(0, 8));
                  } else {
                    failed++;
                    console.warn('[forceEnableNotify] fail', svc.slice(0, 8), ch.slice(0, 8), e.errMsg || e);
                  }
                  res();
                },
              });
            }))).then(() => {
              console.log(`[forceEnableNotify] 完成: ${enabled}/${total} enabled, ${failed} failed`);
              resolve({ enabled, failed, total });
            });
          });
        },
        fail: (e: any) => {
          console.warn('[forceEnableNotify] getServices fail', e.errMsg || e);
          resolve({ enabled: 0, failed: 0, total: 0 });
        },
      });
    });
  }

  /**
   * 重连成功后调用:
   *   把手表本地缓存的 3 天日常数据 (步数/血压/血氧/血糖/体温...) 全部拉回来,
   *   回调走 handleAutoSync -> dataStorage.saveData -> HTTP 上传到生产 MySQL.
   *
   * 5 秒节流, 防止 onShow 反复触发引起 BLE 拥塞.
   */
  pullHistoryFromWatch(): void {
    const now = Date.now();
    if (now - this.lastPullAt < 5000) {
      console.log('[BleHub] pullHistoryFromWatch 节流跳过');
      return;
    }
    this.lastPullAt = now;

    // 1. 拉日常汇总: 步数/距离/卡路里/睡眠 等聚合数据 (走 type=5/9 回包)
    [0, 1, 2].forEach((day, i) => {
      setTimeout(() => {
        try {
          veepooFeature.veepooSendReadDailyDataManager({ day, package: 1 });
          console.log(`[BleHub] 触发拉日常汇总 day=${day}`);
        } catch (err) {
          console.warn(`[BleHub] 拉日常汇总 day=${day} 失败:`, err);
        }
      }, i * 2000);
    });

    // 2. 拉手动测量数据: 用户在手表上手动按键测量的血压/心率/血氧/体温/血糖/HRV/血液成分.
    //    走开 -> 任何时刻打开小程序 -> 自动补齐 的核心 (CLAUDE.md 用户感知需求).
    //    日常汇总不含手动测量的实时值, 必须用 SendManualMeasurementDataRead 单独拉.
    const dataTypes = [
      { id: 0, name: '血压' },
      { id: 1, name: '心率' },
      { id: 2, name: '血糖' },
      { id: 4, name: '血氧' },
      { id: 5, name: '体温' },
      { id: 7, name: 'HRV' },
      { id: 8, name: '血液成分' },
    ];
    // timestamp = 今天 00:00 (秒级 Unix), SDK 会拉这之后的所有手动测量记录
    const today = new Date();
    today.setHours(0, 0, 0, 0);
    const ts = Math.floor(today.getTime() / 1000);
    dataTypes.forEach((dt, i) => {
      setTimeout(() => {
        try {
          veepooFeature.veepooSendManualMeasurementDataReadManager({
            timestamp: ts,
            dataType: dt.id,
          });
          console.log(`[BleHub] 触发拉手动测量 ${dt.name} dataType=${dt.id} ts=${ts}`);
        } catch (err) {
          console.warn(`[BleHub] 拉手动测量 ${dt.name} 失败:`, err);
        }
      }, 6500 + i * 1200);
    });
  }
}

export const bleHub = new BleHub();
