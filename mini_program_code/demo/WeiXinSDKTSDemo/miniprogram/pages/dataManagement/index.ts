// pages/dataManagement/index.ts
import { dataStorage } from '../../services/dataStorage'
import { dataExport } from '../../services/dataExport'
import type { HealthDataType } from '../../types/healthData'

Page({
  data: {
    dateList: [] as string[],
    selectedDate: '',
    dataTypes: [
      { key: 'step', name: '步数', icon: '👣' },
      { key: 'sleep', name: '睡眠', icon: '😴' },
      { key: 'heartRate', name: '心率', icon: '❤️' },
      { key: 'bloodOxygen', name: '血氧', icon: '🫁' },
      { key: 'bloodPressure', name: '血压', icon: '🩸' },
      { key: 'temperature', name: '体温', icon: '🌡️' },
      { key: 'ecg', name: 'ECG', icon: '📈' },
      { key: 'bloodGlucose', name: '血糖', icon: '🍬' },
      { key: 'bloodLiquid', name: '血液成分', icon: '💉' },
      { key: 'bodyComposition', name: '身体成分', icon: '⚖️' },
      { key: 'daily', name: '日常数据', icon: '📊' }
    ],
    currentData: null as any,
    currentDataType: '',
    showDetail: false,
    syncStatus: '',
    serverUrl: 'http://localhost:3000'
  },

  onLoad() {
    this.loadDateList()
  },

  onShow() {
    this.loadDateList()
  },

  // 加载日期列表
  loadDateList() {
    const dates = dataStorage.getDateList()
    this.setData({
      dateList: dates,
      selectedDate: dates.length > 0 ? dates[0] : ''
    })
  },

  // 选择日期
  selectDate(e: any) {
    const date = e.currentTarget.dataset.date
    this.setData({
      selectedDate: date,
      showDetail: false,
      currentData: null,
      currentDataType: ''
    })
  },

  // 查看某类数据
  viewData(e: any) {
    const type = e.currentTarget.dataset.type as HealthDataType
    const data = dataStorage.readTypeData(type, this.data.selectedDate)

    this.setData({
      currentData: data,
      currentDataType: type,
      showDetail: true
    })
  },

  // 关闭详情
  closeDetail() {
    this.setData({
      showDetail: false,
      currentData: null,
      currentDataType: ''
    })
  },

  // 导出当天数据
  async exportDayData() {
    if (!this.data.selectedDate) {
      wx.showToast({ title: '请先选择日期', icon: 'none' })
      return
    }

    try {
      const filePath = await dataExport.exportDataByDateRange(
        this.data.selectedDate,
        this.data.selectedDate
      )
      wx.showToast({ title: '导出成功', icon: 'success' })
      console.log('[DataManagement] 导出文件:', filePath)
    } catch (error) {
      wx.showToast({ title: '导出失败', icon: 'none' })
      console.error('[DataManagement] 导出失败:', error)
    }
  },

  // 导出所有数据
  async exportAllData() {
    try {
      const filePath = await dataExport.exportAllData()
      wx.showToast({ title: '导出成功', icon: 'success' })
      console.log('[DataManagement] 导出所有数据:', filePath)
    } catch (error) {
      wx.showToast({ title: '导出失败', icon: 'none' })
      console.error('[DataManagement] 导出失败:', error)
    }
  },

  // 分享数据文件
  async shareData() {
    if (!this.data.selectedDate) {
      wx.showToast({ title: '请先选择日期', icon: 'none' })
      return
    }

    try {
      const filePath = await dataExport.exportDataByDateRange(
        this.data.selectedDate,
        this.data.selectedDate
      )
      await dataExport.shareViaWeChat(filePath)
    } catch (error) {
      wx.showToast({ title: '分享失败', icon: 'none' })
      console.error('[DataManagement] 分享失败:', error)
    }
  },

  // 同步到服务器（重新发送本地数据）
  async syncToServer() {
    this.setData({ syncStatus: '同步中...' })

    try {
      const dates = dataStorage.getDateList()
      let syncCount = 0

      for (const date of dates) {
        const allData = dataStorage.readDateData(date)
        for (const [type, fileContent] of Object.entries(allData)) {
          if (fileContent && fileContent.records && fileContent.records.length > 0) {
            for (const record of fileContent.records) {
              await this.postToServer(type as HealthDataType, record, date)
              syncCount++
            }
          }
        }
      }

      this.setData({ syncStatus: `同步完成 (${syncCount} 条)` })
      wx.showToast({ title: `同步 ${syncCount} 条数据`, icon: 'success' })
    } catch (error) {
      this.setData({ syncStatus: '同步失败' })
      wx.showToast({ title: '同步失败', icon: 'none' })
    }
  },

  // 发送单条数据到服务器
  postToServer(dataType: HealthDataType, data: any, date: string): Promise<void> {
    return new Promise((resolve, reject) => {
      wx.request({
        url: `${this.data.serverUrl}/api/health-data`,
        method: 'POST',
        data: { dataType, data, date },
        success: (res) => {
          if (res.statusCode === 200 || res.statusCode === 201) {
            resolve()
          } else {
            reject(new Error(`HTTP ${res.statusCode}`))
          }
        },
        fail: (err) => reject(err)
      })
    })
  },

  // 清除当天数据
  clearDayData() {
    if (!this.data.selectedDate) {
      wx.showToast({ title: '请先选择日期', icon: 'none' })
      return
    }

    wx.showModal({
      title: '确认删除',
      content: `确定要删除 ${this.data.selectedDate} 的所有数据吗？`,
      success: (res) => {
        if (res.confirm) {
          dataStorage.clearDateData(this.data.selectedDate)
          this.loadDateList()
          wx.showToast({ title: '删除成功', icon: 'success' })
        }
      }
    })
  },

  // 清除所有数据
  clearAllData() {
    wx.showModal({
      title: '确认删除',
      content: '确定要删除所有健康数据吗？此操作不可恢复！',
      success: (res) => {
        if (res.confirm) {
          dataStorage.clearAllData()
          this.loadDateList()
          this.setData({
            selectedDate: '',
            currentData: null,
            showDetail: false
          })
          wx.showToast({ title: '删除成功', icon: 'success' })
        }
      }
    })
  },

  // 修改服务器地址
  changeServerUrl(e: any) {
    this.setData({
      serverUrl: e.detail.value
    })
  },

  // 测试服务器连接
  testConnection() {
    this.setData({ syncStatus: '测试连接中...' })

    wx.request({
      url: `${this.data.serverUrl}/api/status`,
      method: 'GET',
      timeout: 5000,
      success: (res) => {
        if (res.statusCode === 200) {
          this.setData({ syncStatus: '连接成功' })
          wx.showToast({ title: '服务器连接正常', icon: 'success' })
        } else {
          this.setData({ syncStatus: '连接失败' })
          wx.showToast({ title: '服务器响应异常', icon: 'none' })
        }
      },
      fail: () => {
        this.setData({ syncStatus: '连接失败' })
        wx.showToast({ title: '无法连接服务器', icon: 'none' })
      }
    })
  },

  // 获取数据类型名称
  getDataTypeName(type: string): string {
    const found = this.data.dataTypes.find(item => item.key === type)
    return found ? found.name : type
  }
})
