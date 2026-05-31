// pages/waveform/index.ts


// 定义一个回调类型
type DataChangeCallback = (data: any) => void;

// 父类：蓝牙监听器
abstract class BluetoothListener {
    // 假设有一个方法用于开始监听蓝牙数据（这里仅作为示例）
    startListening(): void {
        // 模拟蓝牙数据接收
        setInterval(() => {
            const mockBluetoothData = this.getMockBluetoothData();
            this.handleBluetoothData(mockBluetoothData);
        }, 10000); // 每秒接收一次数据（仅作为示例）
    }

    // 模拟获取蓝牙数据的方法（在实际应用中，这将是从蓝牙设备接收数据）
    protected abstract getMockBluetoothData(): any;

    // 根据蓝牙数据调用相应的子类方法
    protected abstract handleBluetoothData(data: any): void;
}

// 子类：健康数据处理类
class HealthDataProcessor extends BluetoothListener {
    private onChangeCallback?: DataChangeCallback;

    // 注册数据变化回调
    registerOnChange(callback: DataChangeCallback): void {
        this.onChangeCallback = callback;
    }

    // 模拟获取蓝牙数据的方法（在实际应用中，这将是从父类接收的数据）
    protected getMockBluetoothData(): { type: string, payload: any } {
        // 这里返回模拟的蓝牙数据，包含类型和有效载荷
        return { type: 'HEALTH_DATA', payload: { heartRate: 72, data:[] } };
    }

    // 根据蓝牙数据调用相应的方法
    protected handleBluetoothData(data: { type: string, payload: any }): void {
        if (data.type === 'HEALTH_DATA') {
            this.processHealthData(data.payload);
            // 通知数据变化（如果有回调注册）
            this.onChangeCallback?.(data.payload);
        }
    }

    // 处理健康数据的具体逻辑
    private processHealthData(data: any): void {
        console.log('Processing health data:', data);
        // 这里可以添加实际的健康数据处理逻辑
    }
}

// 主页面类
class MainPage {
    private healthProcessor: HealthDataProcessor;

    constructor() {
        this.healthProcessor = new HealthDataProcessor();
        // 注册数据变化回调
        this.healthProcessor.registerOnChange(this.onHealthDataChange);
        // 开始监听蓝牙数据
        this.healthProcessor.startListening();
    }

    // 数据变化时的回调
    private onHealthDataChange(data: any): void {
        console.log('MainPage received health data change:', data);
        // 这里可以添加更新UI或执行其他操作的逻辑
    }
}

// 使用示例
const mainPage = new MainPage();




Page({

  /**
   * 页面的初始数据
   */
  data: {
    list: [-25.6, -22.3232, -18.432000000000002, -14.745600000000001, -10.854400000000002, -6.9632000000000005, -3.2768, 0.20560000000000003, 3.6872000000000003, 6.964, 9.6264, 12.084, 13.927200000000001, 15.360800000000001, 16.3848, 16.5896, 16.3848, 15.360800000000001, 13.722400000000002, 11.264800000000001, 8.192800000000002, 4.3016000000000005, -0.4096, -5.9392000000000005, -11.878400000000001, -18.432000000000002, -25.395200000000003, 19.865600000000004, 12.4928, 5.12, -1.6392, -8.3976, -14.951200000000002, -20.890400000000003, 25.5992, 20.274400000000004, 15.359200000000001, 10.8536, 6.962400000000001, 3.4808000000000003, 0.4088, -2.2544, -4.097600000000001, -5.531200000000001, -6.555200000000001, -7.169600000000001, -7.579200000000001, -7.784, -7.784, -7.579200000000001, -7.3744000000000005, -6.9648, -6.76, -6.3504000000000005, -5.736, -5.121600000000001, -4.3024000000000004, -3.4832000000000005, -2.8688000000000002, -2.0496, -1.0256, -0.0016, 1.0232, 2.0472, 3.0712, 4.3, 5.5288, 6.757600000000001, 7.781600000000001, 8.8056, 10.034400000000002, 11.263200000000001, 12.2872, 13.311200000000001, 14.3352, 15.359200000000001, 16.383200000000002, 17.4072, 18.2264],
    centerY:75
  },

  /**
   * 生命周期函数--监听页面加载
   */
  onLoad() {
    const res = wx.getSystemInfoSync();
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

    this.drawSmallGrid();
    this.drawCurve()
  },
  drawSmallGrid() {

    const ctx = wx.createCanvasContext('ecg')
    // 设置线条样式
    ctx.setStrokeStyle('#000000') //
    ctx.setLineWidth(0.5) // 线条宽度
    // 将内容绘制到canvas上
    for (var x = 0.5; x < 150; x += 10) {
      ctx.moveTo(x, 0)
      // 结束点
      ctx.lineTo(x, 150)
      // 描边，不调用stroke则看不到画的内容
      ctx.stroke()
    }
    for (var y = 0.5; y < 150; y += 10) {
      ctx.moveTo(0, y)
      // 结束点
      ctx.lineTo(150, y)
      // 描边，不调用stroke则看不到画的内容
      ctx.stroke()
    }
    ctx.draw()

    // 在微信小程序，使用canvas，绘制网格，分为大网格跟小网格，大网格内有5个小网格，5个大网格为1秒，入0秒，y轴颜色加深，1秒，y轴颜色加深

    this.drawEcg2()
    return;
  },

  // drawECG2
  drawEcg2() {
    const ctx = wx.createCanvasContext('ecg2')

    // 设置线条样式
    ctx.setStrokeStyle('red')
    ctx.setLineWidth(0.5) // 线条宽度
    for (var x = 0; x < 150; x += 149) {
      ctx.moveTo(x, 0)
      // 结束点
      ctx.lineTo(x, 160)
      // 描边，不调用stroke则看不到画的内容
      ctx.stroke()
      // 描边，不调用stroke则看不到画的内容
      ctx.stroke()
      // 描边，不调用stroke则看不到画的内容
      ctx.stroke()
    }
    ctx.draw()
  },
  drawCurve: function () {
    let ctx = wx.createCanvasContext('myCanvas', this)
    let list = this.data.list;
    console.log("list==>", list)
    let centerY = this.data.centerY;
    let width = 150;
    let xScale = width / (list.length - 1); // 计算每个数据点占据的宽度
    let yScale = centerY / (Math.max(...list) - Math.min(...list))

    let x = 0;
    let y = centerY / 2 - (list[0] * yScale)
    console.log("xScale=>",xScale)
    console.log("yScale=>",yScale)
    console.log("x=>",x)
    console.log("y=>",y)
    ctx.beginPath();// 开始绘制
    ctx.moveTo(x, y);
    ctx.setStrokeStyle('#c96d79'); // 设置线条颜色
    ctx.setLineWidth(2); // 设置线条宽度
    for (let i = 1; i < list.length; i++) {
      let x = i * xScale;
      let y = centerY / 2 - ((list[i] / 2) * yScale); // 负数在中心下方，正数在中心上方
      ctx.lineTo(x, y);
    }
    ctx.stroke(); // 绘制线条
    ctx.draw(false); // 绘制到canvas上，不需要等待上一步绘制完成
  }


})