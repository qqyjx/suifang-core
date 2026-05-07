/**
 * 数据存储服务
 * 负责将健康数据保存到小程序文件系统，并尝试同步到WSL本地HTTP服务器
 */

import {
  HealthDataType,
  StepData,
  SleepData,
  HeartRateData,
  BloodOxygenData,
  BloodPressureData,
  TemperatureData,
  ECGData,
  BloodGlucoseData,
  BloodLiquidData,
  BodyCompositionData,
  DailyData,
  HealthRecord,
  DataFileContent
} from '../types/healthData';
import { ENV } from './env';

// 后端服务器地址（体验版/正式版统一从 services/env.ts 读取）
const WSL_SERVER_URL = ENV.API_BASE;

// 数据存储根目录
const DATA_ROOT = `${wx.env.USER_DATA_PATH}/health_data`;

class DataStorageService {
  private static readonly PENDING_KEY = 'sync_pending_queue';
  private static readonly MAX_QUEUE = 500;
  // 2h 批量上传一次, 23:59 兜底再清一次 (用户需求: 不实时打数据库, 减压).
  private static readonly BATCH_INTERVAL_MS = 2 * 3600 * 1000;

  private fs: WechatMiniprogram.FileSystemManager;
  private httpEnabled: boolean = true;
  // BLE 连接成功后由 resolveDeviceId() 异步填充，断开时由 resetDeviceIdCache() 清空
  private deviceIdCache: number | null = null;
  private batchTimer: any = null;
  private endOfDayTimer: any = null;

  constructor() {
    this.fs = wx.getFileSystemManager();
    this.initDataDirectory();
  }

  /**
   * 初始化数据目录
   */
  private initDataDirectory(): void {
    try {
      this.fs.accessSync(DATA_ROOT);
    } catch (e) {
      try {
        this.fs.mkdirSync(DATA_ROOT, true);
        console.log('[DataStorage] 数据目录创建成功:', DATA_ROOT);
      } catch (err) {
        console.error('[DataStorage] 创建数据目录失败:', err);
      }
    }
  }

  /**
   * 获取当前日期字符串 (YYYY-MM-DD)
   */
  private getDateString(date?: Date): string {
    const d = date || new Date();
    const year = d.getFullYear();
    const month = String(d.getMonth() + 1).padStart(2, '0');
    const day = String(d.getDate()).padStart(2, '0');
    return `${year}-${month}-${day}`;
  }

  /**
   * 获取ISO格式时间戳
   */
  private getTimestamp(): string {
    return new Date().toISOString();
  }

  /**
   * 获取日期数据目录路径
   */
  private getDateDir(date?: string): string {
    const dateStr = date || this.getDateString();
    return `${DATA_ROOT}/${dateStr}`;
  }

  /**
   * 确保日期目录存在
   */
  private ensureDateDir(date?: string): string {
    const dateDir = this.getDateDir(date);
    try {
      this.fs.accessSync(dateDir);
    } catch (e) {
      try {
        this.fs.mkdirSync(dateDir, true);
      } catch (err) {
        console.error('[DataStorage] 创建日期目录失败:', err);
      }
    }
    return dateDir;
  }

  /**
   * 获取数据类型对应的文件名
   */
  private getFileName(type: HealthDataType): string {
    const fileNames: Record<HealthDataType, string> = {
      step: 'step.json',
      sleep: 'sleep.json',
      heartRate: 'heartRate.json',
      bloodOxygen: 'bloodOxygen.json',
      bloodPressure: 'bloodPressure.json',
      temperature: 'temperature.json',
      ecg: 'ecg.json',
      bloodGlucose: 'bloodGlucose.json',
      bloodLiquid: 'bloodLiquid.json',
      bodyComposition: 'bodyComposition.json',
      daily: 'daily.json'
    };
    return fileNames[type];
  }

  /**
   * 读取数据文件
   */
  private readDataFile<T>(filePath: string): DataFileContent<T> | null {
    try {
      const content = this.fs.readFileSync(filePath, 'utf8') as string;
      return JSON.parse(content) as DataFileContent<T>;
    } catch (e) {
      return null;
    }
  }

  /**
   * 写入数据文件
   */
  private writeDataFile<T>(filePath: string, data: DataFileContent<T>): boolean {
    try {
      this.fs.writeFileSync(filePath, JSON.stringify(data, null, 2), 'utf8');
      return true;
    } catch (e) {
      console.error('[DataStorage] 写入文件失败:', e);
      return false;
    }
  }

  /**
   * 保存健康数据（核心方法）
   * @param type 数据类型
   * @param data 数据内容
   * @param date 可选日期，默认为今天
   */
  async saveData<T extends HealthRecord>(
    type: HealthDataType,
    data: Omit<T, 'timestamp'>,
    date?: string
  ): Promise<boolean> {
    const dateStr = date || this.getDateString();
    const dateDir = this.ensureDateDir(dateStr);
    const fileName = this.getFileName(type);
    const filePath = `${dateDir}/${fileName}`;

    // 添加时间戳
    const record: T = {
      ...data,
      timestamp: this.getTimestamp()
    } as T;

    // 读取现有数据或创建新文件
    let fileContent = this.readDataFile<T>(filePath);
    if (!fileContent) {
      fileContent = {
        date: dateStr,
        records: []
      };
    }

    // 添加新记录
    fileContent.records.push(record);
    fileContent.lastUpdated = this.getTimestamp();

    // 保存到本地文件系统
    const localSaved = this.writeDataFile(filePath, fileContent);

    // 入待上传队列, 不实时上传; 2h 定时器 / 23:59 兜底 / app.onShow 触发 batch flush.
    if (this.httpEnabled) {
      void this.enqueueForBatch(type, record, dateStr);
    }

    console.log(`[DataStorage] 保存${type}数据 (入待传队列):`, localSaved ? '成功' : '失败');
    return localSaved;
  }

  /**
   * 把一条采集记录处理: 立即尝试 POST + 失败入 pending 队列兜底.
   *
   * 5.06-v4 实测发现: 纯 batch 模式 (saveData 只入队, 等 2h flush) 实战不可靠
   * — 前台运行时 batch 定时器不一定按预期触发 (微信小程序 setInterval 在 page
   * 切换/jieli SDK 异常等场景下可能被打断), 数据卡在队列里就是不传.
   *
   * 改为双保险:
   *   1. 立即 POST 一次 (POST 成功 -> 数据立即入库, 主路径)
   *   2. 同时入 pending 队列 (POST 失败 -> 留队列, 2h batch / 23:59 / app.onShow 兜底)
   *   3. POST 成功后从队列里移除 (避免重复上传)
   *
   * 仍然带 recordedAt (采集时刻) + 让 server 加 uploadedAt (上传时刻).
   */
  private async enqueueForBatch<T>(type: HealthDataType, data: T, date: string): Promise<void> {
    const deviceId = await this.resolveDeviceId();
    // 5.06-v9: 患者门诊号由首页"切换患者"输入, 存 storage. 没输入时为空字符串,
    // 服务端 v9 会把空门诊号当作"不写 '门诊号' 字段", 数据照常入库 (兼容老客户端).
    const patientNo: string = (wx.getStorageSync('patientNo') as string) || '';
    const payload: any = {
      dataType: type,
      data,
      date,
      patientId: 1,
      deviceId,
      patientNo,        // 5.06-v9: 主识别字段
      recordedAt: this.getTimestamp(),
    };
    // 先入队 (立即写 storage, 防 POST 没回时小程序被挂起丢数据)
    this.enqueuePending(payload);
    // mac 未就绪 -> 不上传, 等 BleHub.handleAutoSync 收到 type=1 触发 flushPending.
    // patientNo 即使为空也照常上传 (历史 deviceId=4/6/7 的数据就没门诊号, 兼容).
    if (deviceId < 0) {
      console.log(`[DataStorage] mac 未就绪, ${type} 暂留 pending 等 type=1 回包后自动重传`);
      return;
    }
    // 立即试一次 POST. 成功 -> 从队列移除; 失败 -> 留队列等 batch flush 重试.
    try {
      await this.postOnce(payload);
      this.removeFromPending(payload);
      console.log(`[DataStorage] 实时上传 ${type} 成功 (deviceId=${deviceId}, 门诊号=${patientNo || 'NULL'})`);
    } catch (err) {
      console.log(`[DataStorage] 实时上传 ${type} 失败, 留 pending 等 batch 重试:`, err);
    }
  }

  /**
   * POST 成功后从 pending 队列移除指定记录, 避免 batch 时重复上传.
   * 用 dataType + recordedAt 联合匹配 (recordedAt ms 级时间戳, 同 type 同时刻冲突概率极低).
   */
  private removeFromPending(payload: any): void {
    const queue: any[] = wx.getStorageSync(DataStorageService.PENDING_KEY) || [];
    const idx = queue.findIndex(q =>
      q.dataType === payload.dataType &&
      q.recordedAt === payload.recordedAt
    );
    if (idx >= 0) {
      queue.splice(idx, 1);
      wx.setStorageSync(DataStorageService.PENDING_KEY, queue);
    }
  }

  /**
   * 获取所有日期的所有数据
   */
  getAllData(): Record<string, Record<HealthDataType, DataFileContent<any> | null>> {
    const dates = this.getDateList();
    const allData: Record<string, Record<HealthDataType, DataFileContent<any> | null>> = {};
    for (const date of dates) {
      allData[date] = this.readDateData(date);
    }
    return allData;
  }

  /**
   * 同步数据到 HTTP 服务器
   * 单次失败入 pending 队列；下次 onAppShow / 网络恢复时由 flushPending() 补传
   */
  private async syncToServer<T>(type: HealthDataType, data: T, date: string): Promise<void> {
    const deviceId = await this.resolveDeviceId();
    const payload = {
      dataType: type,
      data,
      date,
      // TODO(医生端集成): 当前医院手工绑定，由六元 patients 表管理
      patientId: 1,
      deviceId
    };
    try {
      await this.postOnce(payload);
      console.log(`[DataStorage] HTTP同步${type}成功 (deviceId=${deviceId})`);
    } catch (err) {
      console.log(`[DataStorage] HTTP同步${type}失败入队待补传:`, err);
      this.enqueuePending(payload);
    }
  }

  /**
   * 包装 wx.request 为 Promise，2xx 成功，其他视为失败.
   * 每次实际 POST 时给 payload 加 uploadedAt (上传时间),
   * 配合 enqueueForBatch 写入的 recordedAt (采集时间), 服务端两个时间都能记录.
   */
  private postOnce(payload: any): Promise<void> {
    const finalPayload = { ...payload, uploadedAt: this.getTimestamp() };
    return new Promise((resolve, reject) => {
      wx.request({
        url: `${WSL_SERVER_URL}/api/health-data`,
        method: 'POST',
        data: finalPayload,
        success: (res: any) => {
          if (res.statusCode >= 200 && res.statusCode < 300) resolve();
          else reject(new Error(`HTTP ${res.statusCode}`));
        },
        fail: (err: any) => reject(new Error(err?.errMsg || 'wx.request fail'))
      });
    });
  }

  /**
   * 启动批量上传调度: 每 2h flush + 当天 23:59 兜底 flush.
   *
   * 稳定性策略 (用户强调"链路不能挂"): 单一定时器小程序后台会冻结,
   * 所以叠加多重 flush 触发, 任一种能跑到都行:
   *   1. 2h setInterval (前台运行时)
   *   2. 23:59 setTimeout (前台运行时, 算 delay 到 23:59:00)
   *   3. app.onShow 触发 (在 app.ts 里, 用户开关小程序就 flush)
   *   4. 网络从离线恢复触发 (在 app.ts onNetworkStatusChange)
   *   5. BLE 连接 / 重连成功触发 (待加, 由 bleConnection / app.onShow 重连成功调用)
   *   6. pending 队列堆到 80 条强制 flush (兜底, 防 BLE 长断 + 小程序长时间不用)
   * 调用方 (app.ts onLaunch + onShow) 反复调本方法是幂等的:
   * 每次都先 clear 旧 timer 再设新 timer, 不会泄漏.
   */
  public startBatchSync(): void {
    // 清旧 timer 避免泄漏
    if (this.batchTimer) { clearInterval(this.batchTimer); this.batchTimer = null; }
    if (this.endOfDayTimer) { clearTimeout(this.endOfDayTimer); this.endOfDayTimer = null; }

    // 启动 2h 定时
    this.batchTimer = setInterval(() => {
      console.log('[DataStorage] 2h 定时 flush 触发');
      void this.flushPending().then(r => {
        if (r.ok > 0 || r.fail > 0) console.log('[DataStorage] 2h flush 结果:', r);
      });
    }, DataStorageService.BATCH_INTERVAL_MS);

    // 23:59 兜底
    this.scheduleEndOfDayFlush();

    // 启动时立即 flush 一次 (启动后第一拨堆积一次性清掉)
    void this.flushPending().then(r => {
      if (r.ok > 0 || r.fail > 0) console.log('[DataStorage] 启动 flush 结果:', r);
    });
    console.log(`[DataStorage] 批量调度已启动 (2h 定时 + 23:59 兜底)`);
  }

  /**
   * 算到当天 23:59:00 的剩余 ms, setTimeout 触发一次 flushPending,
   * 跑完递归排明天的 23:59. 防止跨日漏数据.
   */
  private scheduleEndOfDayFlush(): void {
    if (this.endOfDayTimer) { clearTimeout(this.endOfDayTimer); this.endOfDayTimer = null; }
    const now = new Date();
    const eod = new Date(now);
    eod.setHours(23, 59, 0, 0);
    let delay = eod.getTime() - now.getTime();
    if (delay <= 0) delay += 24 * 3600 * 1000; // 已过 23:59, 排到明天
    this.endOfDayTimer = setTimeout(() => {
      console.log('[DataStorage] 23:59 兜底 flush 触发');
      void this.flushPending().then(r => {
        if (r.ok > 0 || r.fail > 0) console.log('[DataStorage] 23:59 flush 结果:', r);
        // 再排明天
        this.scheduleEndOfDayFlush();
      });
    }, delay);
    console.log(`[DataStorage] 23:59 兜底已排, ${Math.round(delay / 60000)} 分钟后触发`);
  }

  /**
   * 根据已连接 BLE 设备解析稳定 deviceId. 唯一可信身份: bleInfo.mac (来自 Veepoo
   * SDK 蓝牙密码核准回调 type=1 里的 VPDeviceMAC, BleHub 写入 storage).
   *
   * !!! 关键设计: mac 没就绪时绝不 register !!!
   *   过去版本会 fallback 用 iOS 代理 UUID 当 stableId 拼 sign, iOS 上 UUID 每次
   *   重装/重连都可能变, 导致 wearable_device 表里同一张手表有多行 (deviceId 漂移).
   *   5.06-v5 实测: 用户测试时数据进了 deviceId=5 而不是预期的 deviceId=4,
   *   就是因为 mac 还没回包前 enqueueForBatch 调了 resolveDeviceId, fallback 到 UUID.
   *
   * 新策略:
   *   - mac 就绪 -> 正常 register, 拿到稳定 deviceId 缓存
   *   - mac 未到 -> 返回 -1, 调用方 (enqueueForBatch) 把数据入 pending 不上传
   *   - BleHub.handleAutoSync 收到 type=1 写 mac 后调 dataStorage.flushPending,
   *     pending 队列里 deviceId=-1 的项重新 resolve + 上传
   *
   * 服务端 /api/device/register 同时收 deviceSign + mac, 优先按 mac 匹配
   * (即便 sign 不一致, 同 mac 永远映射到同一 deviceId, 满足 "一表一行" 的医院多设备需求).
   */
  private async resolveDeviceId(): Promise<number> {
    if (this.deviceIdCache !== null && this.deviceIdCache > 0) return this.deviceIdCache;
    const bleInfo: any = wx.getStorageSync('bleInfo');
    if (!bleInfo || !bleInfo.mac) {
      // mac 未就绪 -> 不 register, 让数据先入队. type=1 回包后 BleHub 会触发 flush.
      return -1;
    }
    // 剥 "(上次连接)" 等 UI 后缀, 防 sign 污染漂移
    const baseName = String(bleInfo.name || 'unknown').replace(/(\s*\(上次连接\))+$/g, '');
    const sign = `${baseName}_${bleInfo.mac}`;
    return new Promise((resolve) => {
      wx.request({
        url: `${WSL_SERVER_URL}/api/device/register`,
        method: 'POST',
        data: { deviceSign: sign, mac: bleInfo.mac, type: 1 },
        success: (res: any) => {
          const id = res?.data?.deviceId;
          if (typeof id === 'number' && id > 0) {
            this.deviceIdCache = id;
            console.log(`[DataStorage] 设备解析成功: deviceId=${id} (sign=${sign}, mac=${bleInfo.mac}, action=${res.data.action})`);
            resolve(id);
          } else {
            console.warn('[DataStorage] register 返回无效 deviceId, 等下次重试:', res?.data);
            resolve(-1);
          }
        },
        fail: (err: any) => {
          console.warn('[DataStorage] register 调用失败, 等下次重试:', err?.errMsg);
          resolve(-1);
        }
      });
    });
  }

  /**
   * BLE 断开时由 bleConnection 页面调用，避免下个连接的表用了旧的 deviceId
   */
  public resetDeviceIdCache(): void {
    this.deviceIdCache = null;
  }

  /**
   * 入待传队列, 超出 MAX_QUEUE 丢最旧的（LRU）.
   * 入队后如果队列堆到阈值 (80 条), 立即 flush 一次, 防止小程序长时间不用导致积压.
   */
  private enqueuePending(payload: any): void {
    const queue: any[] = wx.getStorageSync(DataStorageService.PENDING_KEY) || [];
    queue.push({ ...payload, _enqueuedAt: Date.now() });
    if (queue.length > DataStorageService.MAX_QUEUE) {
      queue.splice(0, queue.length - DataStorageService.MAX_QUEUE);
    }
    wx.setStorageSync(DataStorageService.PENDING_KEY, queue);
    // 队列阈值兜底: 防长断网+长不开小程序导致 2h 定时不触发, 数据无限堆.
    if (queue.length >= 80) {
      console.log(`[DataStorage] pending 队列堆到 ${queue.length} 条, 触发紧急 flush`);
      void this.flushPending();
    }
  }

  /**
   * 把 pending 队列里所有未传成功的数据再发一遍, 由以下场景触发:
   *   - app.ts onAppShow / 网络恢复回调
   *   - BleHub.handleAutoSync 收到 type=1 写 mac 后 (重要: 这是 deviceId=-1 队列项的唯一出路)
   *
   * 5.06-v6 关键: 入队时 mac 未就绪的项 deviceId=-1, 服务端不接受这种值; flush 时必须
   * 重新调 resolveDeviceId 拿真 deviceId 再 POST. 重 resolve 仍失败 (mac 还没到) 就留队列.
   */
  public async flushPending(): Promise<{ ok: number; fail: number }> {
    const queue: any[] = wx.getStorageSync(DataStorageService.PENDING_KEY) || [];
    if (queue.length === 0) return { ok: 0, fail: 0 };
    const remaining: any[] = [];
    let ok = 0;
    for (const item of queue) {
      const { _enqueuedAt, ...payload } = item;
      // deviceId=-1 (入队时 mac 没就绪) -> 重新 resolve. resolve 仍 -1 -> 留队列等下次.
      if (typeof payload.deviceId !== 'number' || payload.deviceId < 0) {
        const did = await this.resolveDeviceId();
        if (did < 0) {
          remaining.push(item);
          continue;
        }
        payload.deviceId = did;
      }
      // 5.06-v9: patientNo 重读 storage 取最新 (用户可能在 enqueue 后切了患者).
      // 即使为空也上传 (NULL 门诊号合法, 与历史数据一致).
      payload.patientNo = (wx.getStorageSync('patientNo') as string) || '';
      try {
        await this.postOnce(payload);
        ok++;
      } catch {
        remaining.push(item);
      }
    }
    wx.setStorageSync(DataStorageService.PENDING_KEY, remaining);
    return { ok, fail: remaining.length };
  }

  /**
   * 保存步数数据
   */
  saveStepData(data: {
    step: number;
    calorie: number;
    distance: number;
  }): Promise<boolean> {
    return this.saveData<StepData>('step', data);
  }

  /**
   * 保存睡眠数据
   */
  saveSleepData(data: {
    fallAsleepTime?: string;
    wakeUpTime?: string;
    deepSleepMinutes: number;
    lightSleepMinutes: number;
    awakeTimes?: number;
    sleepQuality?: number;
    sleepCurve?: number[];
  }): Promise<boolean> {
    return this.saveData<SleepData>('sleep', data);
  }

  /**
   * 保存心率数据
   */
  saveHeartRateData(data: {
    heartRate: number;
    heartState?: number;
  }): Promise<boolean> {
    return this.saveData<HeartRateData>('heartRate', data);
  }

  /**
   * 保存血氧数据
   */
  saveBloodOxygenData(data: {
    bloodOxygen: number;
    heartRate?: number;
  }): Promise<boolean> {
    return this.saveData<BloodOxygenData>('bloodOxygen', data);
  }

  /**
   * 保存血压数据
   */
  saveBloodPressureData(data: {
    systolic: number;
    diastolic: number;
    heartRate?: number;
  }): Promise<boolean> {
    return this.saveData<BloodPressureData>('bloodPressure', data);
  }

  /**
   * 保存体温数据
   */
  saveTemperatureData(data: {
    temperature: number;
    unit?: 'celsius' | 'fahrenheit';
  }): Promise<boolean> {
    return this.saveData<TemperatureData>('temperature', data);
  }

  /**
   * 保存ECG数据
   */
  saveECGData(data: {
    heartRate: number;
    hrv?: number;
    waveformData?: number[];
    diseaseResult?: number[];
  }): Promise<boolean> {
    return this.saveData<ECGData>('ecg', data);
  }

  /**
   * 保存血糖数据
   */
  saveBloodGlucoseData(data: {
    bloodGlucose: number;
    unit?: 'mmol/L' | 'mg/dL';
    mealType?: 'fasting' | 'beforeMeal' | 'afterMeal' | 'random';
  }): Promise<boolean> {
    return this.saveData<BloodGlucoseData>('bloodGlucose', data);
  }

  /**
   * 保存血液成分数据
   */
  saveBloodLiquidData(data: {
    uricAcid?: number;
    cholesterol?: number;
    triglyceride?: number;
    hdl?: number;
    ldl?: number;
  }): Promise<boolean> {
    return this.saveData<BloodLiquidData>('bloodLiquid', data);
  }

  /**
   * 保存身体成分数据
   */
  saveBodyCompositionData(data: {
    weight?: number;
    bmi?: number;
    bodyFatRate?: number;
    muscleMass?: number;
    boneMass?: number;
    waterRate?: number;
    bmr?: number;
    visceralFat?: number;
  }): Promise<boolean> {
    return this.saveData<BodyCompositionData>('bodyComposition', data);
  }

  /**
   * 保存日常综合数据
   */
  saveDailyData(data: {
    step?: number;
    calorie?: number;
    distance?: number;
    heartRate?: number;
    bloodOxygen?: number;
    bloodPressure?: { systolic: number; diastolic: number };
    temperature?: number;
    rr50?: number[];
  }): Promise<boolean> {
    return this.saveData<DailyData>('daily', data);
  }

  /**
   * 读取指定日期的所有数据
   */
  readDateData(date?: string): Record<HealthDataType, DataFileContent<any> | null> {
    const dateStr = date || this.getDateString();
    const dateDir = this.getDateDir(dateStr);
    const types: HealthDataType[] = [
      'step', 'sleep', 'heartRate', 'bloodOxygen', 'bloodPressure',
      'temperature', 'ecg', 'bloodGlucose', 'bloodLiquid', 'bodyComposition', 'daily'
    ];

    const result: Record<string, DataFileContent<any> | null> = {};
    for (const type of types) {
      const fileName = this.getFileName(type);
      const filePath = `${dateDir}/${fileName}`;
      result[type] = this.readDataFile(filePath);
    }

    return result as Record<HealthDataType, DataFileContent<any> | null>;
  }

  /**
   * 读取指定类型的数据
   */
  readTypeData<T>(type: HealthDataType, date?: string): DataFileContent<T> | null {
    const dateStr = date || this.getDateString();
    const dateDir = this.getDateDir(dateStr);
    const fileName = this.getFileName(type);
    const filePath = `${dateDir}/${fileName}`;
    return this.readDataFile<T>(filePath);
  }

  /**
   * 获取所有已保存的日期列表
   */
  getDateList(): string[] {
    try {
      const files = this.fs.readdirSync(DATA_ROOT);
      // 过滤出日期格式的目录 (YYYY-MM-DD)
      return files.filter(f => /^\d{4}-\d{2}-\d{2}$/.test(f)).sort().reverse();
    } catch (e) {
      return [];
    }
  }

  /**
   * 导出所有数据为JSON
   */
  exportAllData(): string {
    const dates = this.getDateList();
    const allData: Record<string, Record<HealthDataType, DataFileContent<any> | null>> = {};

    for (const date of dates) {
      allData[date] = this.readDateData(date);
    }

    return JSON.stringify({
      exportTime: this.getTimestamp(),
      data: allData
    }, null, 2);
  }

  /**
   * 清除指定日期的数据
   */
  clearDateData(date: string): boolean {
    const dateDir = this.getDateDir(date);
    try {
      const files = this.fs.readdirSync(dateDir);
      for (const file of files) {
        this.fs.unlinkSync(`${dateDir}/${file}`);
      }
      this.fs.rmdirSync(dateDir);
      return true;
    } catch (e) {
      console.error('[DataStorage] 清除数据失败:', e);
      return false;
    }
  }

  /**
   * 清除所有数据
   */
  clearAllData(): boolean {
    const dates = this.getDateList();
    let success = true;
    for (const date of dates) {
      if (!this.clearDateData(date)) {
        success = false;
      }
    }
    return success;
  }

  /**
   * 5.06-v8: 蓝牙连接质量埋点. fire-and-forget POST, 失败静默忽略不影响主流程.
   *
   * 调用点位:
   *   - bleConnection.connectBleInternal 5s 后判定握手成功/失败 (event_type=connect_success/handshake_failed)
   *   - bleConnection.connectBleInternal 12s 超时 (event_type=connect_timeout)
   *   - BleHub.tryReconnectOnce 重连成功/三次失败 (event_type=reconnect_success/reconnect_failed)
   *   - BleHub 心跳 90s 暗断 (event_type=heartbeat_timeout)
   *   - BleHub adapter 关 / 开 (event_type=adapter_off/adapter_on)
   *
   * 数据落到服务端 ble_event 表, 后续 GET /api/ble-event/stats?days=7 看聚合.
   */
  reportBleEvent(eventType: string, extra?: any): void {
    try {
      const bleInfo: any = wx.getStorageSync('bleInfo') || {};
      const wxOpenid: string = (wx.getStorageSync('wxOpenid') as string) || '';
      const platform = (() => {
        try {
          const info: any = (wx.getDeviceInfo ? wx.getDeviceInfo() : wx.getSystemInfoSync()) || {};
          return info.platform || '';
        } catch { return ''; }
      })();
      const payload = {
        eventType,
        wxOpenid,
        deviceId: this.deviceIdCache,
        mac: bleInfo.mac || '',
        platform,
        buildTag: WSL_SERVER_URL ? ENV.BUILD_TAG : '', // 防 ENV 未导入时崩
        ts: this.getTimestamp(),
        ...(extra || {}),
      };
      wx.request({
        url: `${WSL_SERVER_URL}/api/ble-event`,
        method: 'POST',
        data: payload,
        // fire-and-forget: 不影响主流程, 失败也不重试
        fail: () => { /* silent */ },
      });
    } catch (err) {
      // 任何异常都不应阻塞蓝牙主流程
    }
  }

  /**
   * 设置是否启用HTTP同步
   */
  setHttpEnabled(enabled: boolean): void {
    this.httpEnabled = enabled;
  }

  /**
   * 获取HTTP同步状态
   */
  isHttpEnabled(): boolean {
    return this.httpEnabled;
  }
}

// 导出单例
export const dataStorage = new DataStorageService();
export default dataStorage;
