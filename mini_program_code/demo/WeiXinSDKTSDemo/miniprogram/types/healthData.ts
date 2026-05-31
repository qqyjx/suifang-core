/**
 * 健康数据类型定义
 * 智能随访小程序 - 数据结构
 */

// 基础记录接口
export interface BaseRecord {
  timestamp: string;  // ISO 8601 格式时间戳
}

// 步数数据
export interface StepRecord extends BaseRecord {
  step: number;       // 步数
  calorie: number;    // 卡路里 (cal)
  distance: number;   // 距离 (米)
}

export interface StepData {
  date: string;       // YYYY-MM-DD
  records: StepRecord[];
}

// 睡眠数据
export interface SleepRecord extends BaseRecord {
  fallAsleepTime: string;    // 入睡时间 HH:mm
  wakeUpTime: string;        // 醒来时间 HH:mm
  deepSleepTime: number;     // 深睡时长 (分钟)
  lightSleepTime: number;    // 浅睡时长 (分钟)
  sleepQuality: number;      // 睡眠质量 0-100
  sleepCurve?: number[];     // 睡眠曲线数据
}

export interface SleepData {
  date: string;
  records: SleepRecord[];
}

// 心率数据
export interface HeartRateRecord extends BaseRecord {
  heartRate: number;      // 心率值 (bpm)
  heartState?: number;    // 心率状态
}

export interface HeartRateData {
  date: string;
  records: HeartRateRecord[];
}

// 血氧数据
export interface BloodOxygenRecord extends BaseRecord {
  bloodOxygen: number;    // 血氧值 (%)
  heartRate?: number;     // 心率值
}

export interface BloodOxygenData {
  date: string;
  records: BloodOxygenRecord[];
}

// 血压数据
export interface BloodPressureRecord extends BaseRecord {
  systolic: number;       // 收缩压 (mmHg)
  diastolic: number;      // 舒张压 (mmHg)
  heartRate?: number;     // 心率值
}

export interface BloodPressureData {
  date: string;
  records: BloodPressureRecord[];
}

// 体温数据
export interface TemperatureRecord extends BaseRecord {
  temperature: number;    // 体温 (°C)
  skinTemperature?: number; // 皮肤温度
}

export interface TemperatureData {
  date: string;
  records: TemperatureRecord[];
}

// ECG数据
export interface ECGRecord extends BaseRecord {
  heartRate: number;      // 心率值
  ecgWaveform?: number[]; // ECG波形数据
  diseaseResult?: number[]; // 疾病分析结果
  hrvData?: number[];     // HRV数据
  progress?: number;      // 测量进度
}

export interface ECGData {
  date: string;
  records: ECGRecord[];
}

// 血糖数据
export interface BloodGlucoseRecord extends BaseRecord {
  bloodGlucose: number;   // 血糖值 (mmol/L)
  mealState?: string;     // 餐前/餐后状态
}

export interface BloodGlucoseData {
  date: string;
  records: BloodGlucoseRecord[];
}

// 血液成分数据
export interface BloodLiquidRecord extends BaseRecord {
  uricAcid?: number;      // 尿酸
  cholesterol?: number;   // 胆固醇
  triacylglycerol?: number; // 甘油三酯
  highDensity?: number;   // 高密度脂蛋白
  lowDensity?: number;    // 低密度脂蛋白
}

export interface BloodLiquidData {
  date: string;
  records: BloodLiquidRecord[];
}

// 身体成分数据
export interface BodyCompositionRecord extends BaseRecord {
  weight?: number;        // 体重 (kg)
  bmi?: number;           // BMI指数
  bodyFat?: number;       // 体脂率 (%)
  muscle?: number;        // 肌肉量 (kg)
  water?: number;         // 水分率 (%)
  bone?: number;          // 骨量 (kg)
  visceralFat?: number;   // 内脏脂肪
  basalMetabolism?: number; // 基础代谢
}

export interface BodyCompositionData {
  date: string;
  records: BodyCompositionRecord[];
}

// 日常综合数据
export interface DailyRecord extends BaseRecord {
  step?: number;
  calorie?: number;
  distance?: number;
  heartRate?: number;
  bloodPressure?: {
    systolic: number;
    diastolic: number;
  };
  bloodOxygen?: number;
  rr50?: number[];        // RR间期数据
}

export interface DailyData {
  date: string;
  records: DailyRecord[];
}

// 健康数据类型枚举
export type HealthDataType =
  | 'step'
  | 'sleep'
  | 'heartRate'
  | 'bloodOxygen'
  | 'bloodPressure'
  | 'temperature'
  | 'ecg'
  | 'bloodGlucose'
  | 'bloodLiquid'
  | 'bodyComposition'
  | 'daily';

// 所有健康数据的联合类型
export type HealthData =
  | StepData
  | SleepData
  | HeartRateData
  | BloodOxygenData
  | BloodPressureData
  | TemperatureData
  | ECGData
  | BloodGlucoseData
  | BloodLiquidData
  | BodyCompositionData
  | DailyData;

// 所有记录的联合类型
export type HealthRecord =
  | StepRecord
  | SleepRecord
  | HeartRateRecord
  | BloodOxygenRecord
  | BloodPressureRecord
  | TemperatureRecord
  | ECGRecord
  | BloodGlucoseRecord
  | BloodLiquidRecord
  | BodyCompositionRecord
  | DailyRecord;

// 导出数据格式
export interface ExportData {
  exportTime: string;
  deviceInfo?: {
    deviceId?: string;
    deviceName?: string;
  };
  data: {
    [date: string]: {
      step?: StepData;
      sleep?: SleepData;
      heartRate?: HeartRateData;
      bloodOxygen?: BloodOxygenData;
      bloodPressure?: BloodPressureData;
      temperature?: TemperatureData;
      ecg?: ECGData;
      bloodGlucose?: BloodGlucoseData;
      bloodLiquid?: BloodLiquidData;
      bodyComposition?: BodyCompositionData;
      daily?: DailyData;
    };
  };
}

// HTTP同步请求数据格式
export interface SyncRequest {
  type: HealthDataType;
  date: string;
  record: HealthRecord;
  deviceId?: string;
}

// HTTP同步响应格式
export interface SyncResponse {
  success: boolean;
  message?: string;
  savedPath?: string;
}
