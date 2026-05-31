/**
 * BLE 通知数据分发器
 *
 * 背景：SDK veepooWeiXinSDKNotifyMonitorValueChange 是单监听机制，
 * 同一时刻只能有一个 page 的回调被触发。患者在手表本机自测的数据
 * （血压/血氧/体温/血糖/血液成分/身体成分/步数）会通过 BLE 推到
 * 当前活跃 page 的回调。
 *
 * 患者不会逐个进对应 page，所以默认停留页（pages/index/index）
 * 和连接页（pages/bleConnection/index）必须挂全套 type 分发逻辑，
 * 兜底把数据落库 + 上传到 dc.ncrc.org.cn。
 *
 * 各功能 page（universalBlood / bloodOxygen / bodyTemperature 等）
 * 已有自己的 saveData 调用，本 dispatcher 与之不重复触发
 * （SDK 单监听 → 同一帧只激活一个回调链）。
 */

import { dataStorage } from './dataStorage'

export function dispatchBleData(e: any): void {
  if (!e || typeof e.type === 'undefined') return
  const c = e.content || {}

  switch (e.type) {
    // 体温（来自 bodyTemperature 自动推送）
    case 6:
      if (c.bodyTemperature || c.skinTemperature) {
        dataStorage.saveData('temperature', {
          bodyTemperature: c.bodyTemperature || 0,
          skinTemperature: c.skinTemperature || 0,
          temperatureUnit: c.temperatureUnit || 'celsius'
        })
      }
      break

    // 步数（手表实时推送）
    case 9:
      if (typeof c.step === 'number') {
        dataStorage.saveData('step', {
          step: c.step,
          calorie: c.calorie || 0,
          distance: c.distance || 0
        })
      }
      break

    // 血压（手表本机测量完成后推送）
    case 18: {
      const high = c.bloodPressureHigh ?? c.systolic
      const low = c.bloodPressureLow ?? c.diastolic
      if (high && low) {
        dataStorage.saveData('bloodPressure', {
          systolic: high,
          diastolic: low,
          heartRate: c.heartRate || 0,
          measureStatus: c.measureStatus || 0
        })
      }
      break
    }

    // 血氧单次/全天（type 29 是开关设置，跳过）
    case 30:
      if (c.bloodOxygen) {
        dataStorage.saveData('bloodOxygen', {
          bloodOxygen: c.bloodOxygen,
          heartRate: c.heartRate || 0,
          allDayData: c.allDayData || []
        })
      }
      break

    // 身体成分（体重秤推送）
    case 32:
      if (c.weight || c.bmi) {
        dataStorage.saveData('bodyComposition', {
          weight: c.weight || 0,
          bmi: c.bmi || 0,
          bodyFatRate: c.bodyFatRate || 0,
          muscleRate: c.muscleRate || 0,
          moisture: c.moisture || 0,
          boneMass: c.boneMass || 0,
          visceralFat: c.visceralFat || 0,
          basalMetabolism: c.basalMetabolism || 0,
          proteinRate: c.proteinRate || 0,
          bodyAge: c.bodyAge || 0
        })
      }
      break

    // 血糖
    case 37:
    case 38:
      if (c.bloodGlucose) {
        dataStorage.saveData('bloodGlucose', {
          bloodGlucose: c.bloodGlucose,
          measureTime: c.measureTime || '',
          unit: 'mmol/L'
        })
      }
      break

    // 血液成分
    case 39:
    case 40:
      if (c.uricAcid || c.cholesterol) {
        dataStorage.saveData('bloodLiquid', {
          uricAcid: c.uricAcid || 0,
          cholesterol: c.cholesterol || 0,
          triacylglycerol: c.triacylglycerol || 0,
          highDensity: c.highDensity || 0,
          lowDensity: c.lowDensity || 0
        })
      }
      break
  }
}
