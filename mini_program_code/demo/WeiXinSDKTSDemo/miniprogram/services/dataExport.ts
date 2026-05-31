/**
 * 数据导出服务
 * 将健康数据导出为JSON文件并支持分享
 */

import { dataStorage } from './dataStorage'
import type { HealthDataType } from '../types/healthData'

class DataExportService {
  private fs: WechatMiniprogram.FileSystemManager

  constructor() {
    this.fs = wx.getFileSystemManager()
  }

  /**
   * 导出所有数据为JSON文件
   */
  async exportAllData(): Promise<string> {
    try {
      const allData = dataStorage.getAllData()
      const exportData = {
        exportTime: new Date().toISOString(),
        version: '1.0.0',
        data: allData
      }

      const jsonString = JSON.stringify(exportData, null, 2)
      const fileName = `health_data_export_${this.formatDateForFileName(new Date())}.json`
      const filePath = `${wx.env.USER_DATA_PATH}/export/${fileName}`

      // 确保导出目录存在
      await this.ensureExportDir()

      // 写入文件
      this.fs.writeFileSync(filePath, jsonString, 'utf8')
      console.log('[DataExport] 数据已导出到:', filePath)

      return filePath
    } catch (error) {
      console.error('[DataExport] 导出失败:', error)
      throw error
    }
  }

  /**
   * 导出指定日期范围的数据
   */
  async exportDataByDateRange(startDate: string, endDate: string): Promise<string> {
    try {
      const allData = dataStorage.getAllData()
      const filteredData: Record<string, any> = {}

      // 过滤日期范围内的数据
      Object.keys(allData).forEach(date => {
        if (date >= startDate && date <= endDate) {
          filteredData[date] = allData[date]
        }
      })

      const exportData = {
        exportTime: new Date().toISOString(),
        version: '1.0.0',
        dateRange: { startDate, endDate },
        data: filteredData
      }

      const jsonString = JSON.stringify(exportData, null, 2)
      const fileName = `health_data_${startDate}_to_${endDate}.json`
      const filePath = `${wx.env.USER_DATA_PATH}/export/${fileName}`

      await this.ensureExportDir()
      this.fs.writeFileSync(filePath, jsonString, 'utf8')
      console.log('[DataExport] 数据已导出到:', filePath)

      return filePath
    } catch (error) {
      console.error('[DataExport] 导出失败:', error)
      throw error
    }
  }

  /**
   * 导出指定类型的数据
   */
  async exportDataByType(dataType: HealthDataType): Promise<string> {
    try {
      const allData = dataStorage.getAllData()
      const typeData: Record<string, any> = {}

      // 提取指定类型的数据
      Object.keys(allData).forEach(date => {
        const dayData = allData[date]
        if (dayData && dayData[dataType]) {
          typeData[date] = dayData[dataType]
        }
      })

      const exportData = {
        exportTime: new Date().toISOString(),
        version: '1.0.0',
        dataType: dataType,
        data: typeData
      }

      const jsonString = JSON.stringify(exportData, null, 2)
      const fileName = `health_${dataType}_${this.formatDateForFileName(new Date())}.json`
      const filePath = `${wx.env.USER_DATA_PATH}/export/${fileName}`

      await this.ensureExportDir()
      this.fs.writeFileSync(filePath, jsonString, 'utf8')
      console.log('[DataExport] 数据已导出到:', filePath)

      return filePath
    } catch (error) {
      console.error('[DataExport] 导出失败:', error)
      throw error
    }
  }

  /**
   * 导出今日数据
   */
  async exportTodayData(): Promise<string> {
    const today = this.getTodayDate()
    return this.exportDataByDateRange(today, today)
  }

  /**
   * 分享导出的文件
   */
  async shareExportedFile(filePath: string): Promise<void> {
    try {
      // 读取文件内容
      const content = this.fs.readFileSync(filePath, 'utf8') as string

      // 复制到剪贴板
      await wx.setClipboardData({
        data: content
      })

      wx.showToast({
        title: '数据已复制到剪贴板',
        icon: 'success'
      })
    } catch (error) {
      console.error('[DataExport] 分享失败:', error)
      throw error
    }
  }

  /**
   * 通过微信分享文件（需要使用wx.shareFileMessage，仅在某些场景可用）
   */
  async shareViaWeChat(filePath: string): Promise<void> {
    try {
      // 注意：wx.shareFileMessage 仅在部分场景可用
      if (typeof wx.shareFileMessage === 'function') {
        await wx.shareFileMessage({
          filePath: filePath,
          fileName: filePath.split('/').pop() || 'health_data.json'
        })
      } else {
        // 回退方案：复制到剪贴板
        await this.shareExportedFile(filePath)
      }
    } catch (error) {
      console.error('[DataExport] 微信分享失败:', error)
      // 回退方案
      await this.shareExportedFile(filePath)
    }
  }

  /**
   * 在控制台打印所有数据（调试用）
   */
  printAllDataToConsole(): void {
    const allData = dataStorage.getAllData()
    console.log('========== 健康数据导出 ==========')
    console.log(JSON.stringify(allData, null, 2))
    console.log('==================================')
  }

  /**
   * 获取导出文件列表
   */
  getExportedFiles(): string[] {
    try {
      const exportDir = `${wx.env.USER_DATA_PATH}/export`
      const files = this.fs.readdirSync(exportDir)
      return files.filter(f => f.endsWith('.json')).map(f => `${exportDir}/${f}`)
    } catch (error) {
      return []
    }
  }

  /**
   * 删除导出文件
   */
  deleteExportedFile(filePath: string): void {
    try {
      this.fs.unlinkSync(filePath)
      console.log('[DataExport] 已删除文件:', filePath)
    } catch (error) {
      console.error('[DataExport] 删除文件失败:', error)
    }
  }

  /**
   * 清理所有导出文件
   */
  clearAllExportedFiles(): void {
    const files = this.getExportedFiles()
    files.forEach(file => this.deleteExportedFile(file))
  }

  /**
   * 获取数据统计信息
   */
  getDataStatistics(): {
    totalDays: number
    dataTypes: string[]
    totalRecords: number
    dateRange: { earliest: string; latest: string } | null
  } {
    const allData = dataStorage.getAllData()
    const dates = Object.keys(allData).sort()
    const dataTypes = new Set<string>()
    let totalRecords = 0

    Object.values(allData).forEach(dayData => {
      Object.keys(dayData).forEach(type => {
        dataTypes.add(type)
        const typeData = dayData[type]
        if (typeData && typeData.records) {
          totalRecords += typeData.records.length
        }
      })
    })

    return {
      totalDays: dates.length,
      dataTypes: Array.from(dataTypes),
      totalRecords,
      dateRange: dates.length > 0 ? { earliest: dates[0], latest: dates[dates.length - 1] } : null
    }
  }

  /**
   * 确保导出目录存在
   */
  private async ensureExportDir(): Promise<void> {
    const exportDir = `${wx.env.USER_DATA_PATH}/export`
    try {
      this.fs.accessSync(exportDir)
    } catch {
      this.fs.mkdirSync(exportDir, true)
    }
  }

  /**
   * 格式化日期用于文件名
   */
  private formatDateForFileName(date: Date): string {
    const year = date.getFullYear()
    const month = String(date.getMonth() + 1).padStart(2, '0')
    const day = String(date.getDate()).padStart(2, '0')
    const hour = String(date.getHours()).padStart(2, '0')
    const minute = String(date.getMinutes()).padStart(2, '0')
    return `${year}${month}${day}_${hour}${minute}`
  }

  /**
   * 获取今天日期
   */
  private getTodayDate(): string {
    const now = new Date()
    const year = now.getFullYear()
    const month = String(now.getMonth() + 1).padStart(2, '0')
    const day = String(now.getDate()).padStart(2, '0')
    return `${year}-${month}-${day}`
  }
}

export const dataExport = new DataExportService()
